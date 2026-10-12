from __future__ import annotations

import json
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request

import pytest

from longarc.cli import main
from longarc.schwab.auth import (
    AppConfig,
    OAuthSession,
    read_private_json,
    write_private_json,
)
from longarc.schwab.client import SchwabError


def app(directory: Path) -> None:
    write_private_json(
        directory / "app.json",
        {
            "client_id": "test-client",
            "client_secret": "test-secret",
            "redirect_uri": "https://127.0.0.1:8182",
        },
    )


def test_private_files_and_config_repr(tmp_path: Path) -> None:
    app(tmp_path)
    assert stat.S_IMODE((tmp_path / "app.json").stat().st_mode) == 0o600
    assert "test-client" not in repr(AppConfig.load(tmp_path / "app.json"))
    assert "test-secret" not in repr(AppConfig.load(tmp_path / "app.json"))
    (tmp_path / "app.json").chmod(0o644)
    with pytest.raises(SchwabError, match="permissions"):
        AppConfig.load(tmp_path / "app.json")


@pytest.mark.parametrize(
    "callback",
    [
        "http://127.0.0.1:8182",
        "https://example.com",
        "https://user@127.0.0.1",
        "https://127.0.0.1?key=bad",
    ],
)
def test_config_rejects_nonlocal_or_unsafe_callbacks(callback: str) -> None:
    with pytest.raises(SchwabError):
        AppConfig("test-client", "test-secret", callback)


def test_manual_authorization_decodes_code_once_and_keeps_tokens_private(tmp_path: Path) -> None:
    app(tmp_path)
    requests: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        requests.append(request)
        assert timeout == 30
        return json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_in": 1800,
                "token_type": "Bearer",
            }
        ).encode()

    session = OAuthSession(tmp_path, transport=transport, clock=lambda: 1000)
    state = parse_qs(urlsplit(session.start()).query)["state"][0]
    session.finish("https://127.0.0.1:8182/?" + urlencode({"code": "code@", "state": state}))
    assert requests[0].full_url == "https://api.schwabapi.com/v1/oauth/token"
    assert requests[0].get_method() == "POST"
    assert parse_qs(requests[0].data.decode())["code"] == ["code@"]  # type: ignore[union-attr]
    assert session.access_token() == "test-access"
    assert not (tmp_path / "pending-auth.json").exists()
    assert stat.S_IMODE((tmp_path / "token.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "returned",
    [
        "https://127.0.0.1:8182/?code=bad&state=wrong",
        "https://evil.example/?code=bad&state={state}",
        "https://127.0.0.1:8182/?code=bad&code=another&state={state}",
        "https://127.0.0.1:8182/?error=access_denied&state={state}",
    ],
)
def test_rejected_callback_never_exchanges_tokens(tmp_path: Path, returned: str) -> None:
    app(tmp_path)

    def no_transport(request: Request, timeout: float) -> bytes:
        pytest.fail("Invalid callback must not send a token request")

    session = OAuthSession(tmp_path, transport=no_transport, clock=lambda: 1000)
    state = parse_qs(urlsplit(session.start()).query)["state"][0]
    with pytest.raises(SchwabError):
        session.finish(returned.format(state=state))
    assert not (tmp_path / "token.json").exists()


def test_expired_authorization_rejected(tmp_path: Path) -> None:
    app(tmp_path)
    now = [1000.0]
    session = OAuthSession(tmp_path, clock=lambda: now[0])
    session.start()
    now[0] += 3601
    with pytest.raises(SchwabError, match="expired"):
        session.finish("https://127.0.0.1:8182/?code=test&state=unused")


def test_authorization_can_finish_after_reading_terms(tmp_path: Path) -> None:
    app(tmp_path)
    now = [1000.0]

    def transport(request: Request, timeout: float) -> bytes:
        return b'{"access_token":"test-access","refresh_token":"test-refresh","expires_in":1800}'

    session = OAuthSession(tmp_path, transport=transport, clock=lambda: now[0])
    state = parse_qs(urlsplit(session.start()).query)["state"][0]
    now[0] += 21 * 60
    session.finish("https://127.0.0.1:8182/?" + urlencode({"code": "code@", "state": state}))
    assert session.access_token() == "test-access"


def test_refresh_does_not_extend_original_grant(tmp_path: Path) -> None:
    app(tmp_path)
    write_private_json(
        tmp_path / "token.json",
        {
            "client_id": "test-client",
            "redirect_uri": "https://127.0.0.1:8182",
            "access_token": "old",
            "refresh_token": "test-refresh",
            "expires_at": 1100,
            "refresh_created_at": 1000,
        },
    )
    now = [2000.0]

    def refresh(request: Request, timeout: float) -> bytes:
        assert request.data is not None
        assert parse_qs(request.data.decode())["grant_type"] == ["refresh_token"]
        return b'{"access_token":"new","expires_in":1800}'

    session = OAuthSession(tmp_path, transport=refresh, clock=lambda: now[0])
    assert session.access_token() == "new"
    assert read_private_json(tmp_path / "token.json")["refresh_created_at"] == 1000
    now[0] = 1000 + 7 * 86400
    with pytest.raises(SchwabError, match="Refresh token expired"):
        session.access_token()


def test_token_cannot_be_reused_for_another_app(tmp_path: Path) -> None:
    app(tmp_path)
    write_private_json(
        tmp_path / "token.json",
        {
            "client_id": "another-client",
            "redirect_uri": "https://127.0.0.1:8182",
        },
    )
    with pytest.raises(SchwabError, match="another app"):
        OAuthSession(tmp_path).access_token()


def test_cli_status_and_missing_auth_are_safe(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    app(tmp_path / "private/schwab")
    assert main(["schwab", "status"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["token_present"] is False
    assert "test-secret" not in output and "test-client" not in output
    assert main(["schwab", "accounts"]) == 1
    assert "authorize first" in capsys.readouterr().out


def test_cli_rejects_public_output_before_read(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    assert main(["schwab", "--directory", "public", "status"]) == 1
    assert "under ./private/" in capsys.readouterr().out
    app(tmp_path / "private/schwab")
    assert main(["schwab", "accounts", "--output", "accounts.json"]) == 1
    assert "under ./private/" in capsys.readouterr().out
    assert not (tmp_path / "accounts.json").exists()


def test_cli_saves_private_snapshot(
    tmp_path: Path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.chdir(tmp_path)
    app(tmp_path / "private/schwab")
    monkeypatch.setattr(OAuthSession, "access_token", lambda self: "test-access")
    monkeypatch.setattr(
        "longarc.schwab.cli.SchwabReadOnlyClient.accounts",
        lambda self: [{"securitiesAccount": {"accountNumber": "private-account"}}],
    )
    assert main(["schwab", "accounts"]) == 0
    output = capsys.readouterr().out
    assert "private-account" not in output and "test-access" not in output
    files = list((tmp_path / "private/schwab/snapshots").glob("accounts-*.json"))
    assert len(files) == 1
    data = read_private_json(files[0])
    assert data["provider"] == "schwab-api" and data["captured_at"]
    assert data["data"][0]["securitiesAccount"]["accountNumber"] == "private-account"


def test_snapshot_cannot_overwrite_an_existing_private_file(
    tmp_path: Path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.chdir(tmp_path)
    app(tmp_path / "private/schwab")
    target = tmp_path / "private/plan.json"
    target.write_text('{"preserve":"policy"}')
    assert main(["schwab", "accounts", "--output", "private/plan.json"]) == 1
    assert "already exists" in capsys.readouterr().out
    assert target.read_text() == '{"preserve":"policy"}'
    with pytest.raises(FileExistsError):
        write_private_json(target, {"new": "snapshot"}, overwrite=False)
    assert target.read_text() == '{"preserve":"policy"}'
