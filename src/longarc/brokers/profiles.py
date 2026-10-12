"""Explicit login, environment, and account selection; never infer an account index."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from longarc.core.private_files import read_private_json, write_private_json

_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def private_path(value: str | Path) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to((Path.cwd() / "private").resolve()):
        raise ValueError("Broker configuration and snapshots must stay under ./private/.")
    return path


@dataclass(frozen=True)
class BrokerProfile:
    name: str
    broker: str
    login_alias: str
    environment: str
    credential_directory: str
    account_key: str | None = None
    account_id: str | None = None
    expected_account_last4: str | None = None

    def __post_init__(self) -> None:
        if not _ALIAS.fullmatch(self.name) or not _ALIAS.fullmatch(self.login_alias):
            raise ValueError("Profile and login aliases must be short ASCII names.")
        if self.broker not in {"schwab", "etrade"}:
            raise ValueError("Supported brokers are schwab and etrade.")
        if self.environment not in {"production", "sandbox"}:
            raise ValueError("Choose production or sandbox explicitly.")
        if self.broker == "schwab" and self.environment != "production":
            raise ValueError("The Schwab adapter supports production only.")
        private_path(self.credential_directory)
        for value in (self.account_key, self.account_id):
            if value is not None and (not isinstance(value, str) or not _KEY.fullmatch(value)):
                raise ValueError("Invalid broker account binding.")
        if (self.account_key is None) != (self.account_id is None):
            raise ValueError("Account key and account ID must be bound together.")
        if self.expected_account_last4 is not None:
            if not re.fullmatch(r"\d{4}", self.expected_account_last4):
                raise ValueError("Expected account suffix must contain four digits.")
            if self.account_id is not None and not self.account_id.endswith(
                self.expected_account_last4
            ):
                raise ValueError("Account binding conflicts with the expected account suffix.")

    @property
    def directory(self) -> Path:
        return private_path(self.credential_directory)

    def identity(self) -> dict[str, object]:
        return {
            "profile": self.name,
            "broker": self.broker,
            "login_alias": self.login_alias,
            "environment": self.environment,
            "account_key": self.account_key,
            "account_id": self.account_id,
            "expected_account_last4": self.expected_account_last4,
        }

    def require_account(self) -> str:
        if self.account_key is None:
            raise ValueError("Profile has no account binding; save accounts then bind-account.")
        return self.account_key


def load_profiles(path: Path) -> dict[str, BrokerProfile]:
    data = read_private_json(private_path(path))
    records = data.get("profiles")
    if data.get("format") != "broker-profiles-v1" or not isinstance(records, list):
        raise ValueError("Expected a broker-profiles-v1 private configuration.")
    result: dict[str, BrokerProfile] = {}
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("Invalid broker profile.")
        try:
            profile = BrokerProfile(**raw)
        except TypeError:
            raise ValueError("Invalid broker profile fields.") from None
        if profile.name in result:
            raise ValueError("Profile names must be unique.")
        result[profile.name] = profile
    return result


def save_profiles(path: Path, profiles: dict[str, BrokerProfile]) -> None:
    write_private_json(
        private_path(path),
        {
            "format": "broker-profiles-v1",
            "profiles": [asdict(profile) for profile in profiles.values()],
        },
    )
