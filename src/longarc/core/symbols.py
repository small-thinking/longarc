"""Explicit underlying identity; legacy unscoped policies belong only to QQQ."""

from __future__ import annotations

import re
from typing import Any


def canonical_symbol(value: Any) -> str:
    """Validate a ticker without guessing aliases or silently changing its case."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", value):
        raise ValueError("symbol must be an explicit canonical uppercase ticker")
    return value


def policy_symbol(policy: dict[str, Any]) -> str:
    """Old QQQ policies may omit scope; new assets require scope.underlying."""
    if "scope" not in policy:
        return "QQQ"
    scope = policy["scope"]
    if not isinstance(scope, dict):
        raise ValueError("policy.scope must contain an explicit underlying")
    return canonical_symbol(scope.get("underlying"))


def require_policy_symbol(symbol: str, policy: dict[str, Any]) -> None:
    if canonical_symbol(symbol) != policy_symbol(policy):
        raise ValueError("Policy underlying does not match the requested symbol")
