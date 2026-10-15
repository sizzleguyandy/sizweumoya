"""Resolve which runner an MCP call is for, from request headers only.

Each runner's Hermes profile is configured with:
  - identity_header -> sends the profile name (e.g. x-hermes-profile: runner-1001)
  - a static Authorization: Bearer <secret> header unique to that profile

Both must match the runners row. The model never supplies a runner id.
"""
import hashlib
import hmac
import secrets
from collections.abc import Mapping


class AccessDenied(Exception):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    name = name.lower()
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


def resolve_runner(conn, headers: Mapping[str, str] | None, profile_header: str):
    """Return the active runners row for this request or raise AccessDenied."""
    if not headers:
        raise AccessDenied("no request headers")
    profile = _header(headers, profile_header)
    auth = _header(headers, "authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not profile or not token:
        raise AccessDenied("missing profile or token")
    row = conn.execute(
        "SELECT * FROM runners WHERE hermes_profile=? AND active=1", (profile,)
    ).fetchone()
    if row is None or not hmac.compare_digest(row["mcp_token_hash"], hash_token(token)):
        raise AccessDenied("unknown profile or bad token")
    return row
