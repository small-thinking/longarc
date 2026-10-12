from __future__ import annotations

import http.client
import json
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from longarc.schwab.auth import OAuthSession, write_private_json
from longarc.schwab.callback import CallbackServer
from longarc.schwab.client import SchwabError


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def certificate(tmp_path: Path) -> tuple[Path, Path]:
    if shutil.which("openssl") is None:
        pytest.skip("OpenSSL command is needed for the local TLS fixture")
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=127.0.0.1",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
            "-days",
            "1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return certfile, keyfile


def _session(tmp_path: Path, port: int, transport) -> OAuthSession:  # type: ignore[no-untyped-def]
    write_private_json(
        tmp_path / "app.json",
        {
            "client_id": "test-client",
            "client_secret": "test-secret",
            "redirect_uri": f"https://127.0.0.1:{port}",
        },
    )
    return OAuthSession(tmp_path, transport=transport)


def _get(port: int, path: str, host: str | None = None) -> tuple[int, bytes, dict[str, str]]:
    connection = http.client.HTTPSConnection(
        "127.0.0.1", port, timeout=3, context=ssl._create_unverified_context()
    )
    try:
        connection.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def test_callback_accepts_only_registered_host_path_and_state(
    tmp_path: Path, certificate: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    port = _free_port()
    requests: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        requests.append(request)
        return json.dumps(
            {
                "access_token": "private-access",
                "refresh_token": "private-refresh",
                "expires_in": 1800,
            }
        ).encode()

    session = _session(tmp_path, port, transport)
    certfile, keyfile = certificate
    with CallbackServer(session, certfile, keyfile) as callback:
        assert callback.address == ("127.0.0.1", port)
        assert callback.ready_url == f"https://127.0.0.1:{port}/health"
        state = parse_qs(urlsplit(session.start()).query)["state"][0]
        failures: list[Exception] = []

        def wait() -> None:
            try:
                callback.run(timeout=5)
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=wait)
        thread.start()
        try:
            status, body, headers = _get(port, "/health")
            assert status == 200 and body == b"ready\n"
            assert headers["Cache-Control"] == "no-store"
            for path, host in [
                (f"/?code=private-code&state={state}", "evil.example"),
                (f"/other?code=private-code&state={state}", None),
                ("/?code=private-code&state=wrong", None),
                (f"/?code=private-code&state={state}&state={state}", None),
                (f"/?code=private-code&code=other&state={state}", None),
            ]:
                status, body, _ = _get(port, path, host)
                assert status in (400, 404)
                assert b"private-code" not in body
            assert requests == []
            status, body, headers = _get(port, f"/?code=private-code&state={state}")
            assert status == 200
            assert b"private-code" not in body and b"private-access" not in body
            assert headers["Referrer-Policy"] == "no-referrer"
            assert headers["Content-Security-Policy"] == "default-src 'none'"
        finally:
            thread.join(timeout=6)
        assert not thread.is_alive()
        assert failures == []
        assert len(requests) == 1
    output = capsys.readouterr()
    assert "private-code" not in output.out + output.err


def test_exchange_failure_is_static_and_terminal(
    tmp_path: Path, certificate: tuple[Path, Path]
) -> None:
    port = _free_port()

    def transport(request: Request, timeout: float) -> bytes:
        raise SchwabError("secret upstream error")

    session = _session(tmp_path, port, transport)
    certfile, keyfile = certificate
    with CallbackServer(session, certfile, keyfile) as callback:
        state = parse_qs(urlsplit(session.start()).query)["state"][0]
        failures: list[Exception] = []

        def wait() -> None:
            try:
                callback.run(timeout=5)
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=wait)
        thread.start()
        try:
            status, body, _ = _get(port, f"/?code=private-code&state={state}")
            assert status == 500
            assert b"secret upstream error" not in body and b"private-code" not in body
        finally:
            thread.join(timeout=6)
        assert not thread.is_alive()
        assert len(failures) == 1
        assert isinstance(failures[0], SchwabError)
        assert "secret upstream error" not in str(failures[0])


def test_callback_timeout(certificate: tuple[Path, Path], tmp_path: Path) -> None:
    port = _free_port()

    def transport(request: Request, timeout: float) -> bytes:
        pytest.fail("Timeout must not exchange a code")

    session = _session(tmp_path, port, transport)
    with CallbackServer(session, *certificate) as callback:
        session.start()
        with pytest.raises(SchwabError, match="timed out"):
            callback.run(timeout=0.05)
