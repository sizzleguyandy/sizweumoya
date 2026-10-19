"""Resolve which runner an MCP call is for, from request headers only.

Each runner's Hermes profile declares the MCP server in its own ``config.yaml``
(``mcp_servers.coach_data``) with the two things this module checks:

  identity_header: {name: x-hermes-profile, value_from: profile}   # Hermes sends the profile name
  headers: {Authorization: "Bearer <this profile's one-time token>"}

``identity_header`` with ``value_from: profile`` is resolved by Hermes at connect time and
sends the *active profile name* (Hermes v0.22.2: ``tools/mcp_tool_errors.py``
``_resolve_identity_header``, applied in ``tools/mcp_tool_transport.py``). A plain string
value is ignored with a warning and no header is sent, so the mapping form is required.

Both values must match the same ``runners`` row. The profile name is not a secret (another
profile could type it), so the bearer token is what proves identity: only its sha256 is
stored, it is issued once per runner, and it is compared in constant time. Neither value can
be set by the model — they come from the profile's config, so no tool accepts a runner id.
"""
import hashlib
import hmac
import secrets
from collections.abc import Mapping

# A token shorter than this cannot come from new_token(); treat it as a malformed request.
MIN_TOKEN_LEN = 16


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
    """Return the active runners row for this request or raise AccessDenied.

    The profile header names the caller; the bearer token must be the one issued for that
    exact profile. A token from another profile never resolves, and no request can name a
    runner id.
    """
    if not headers:
        raise AccessDenied("no request headers")
    profile = (_header(headers, profile_header) or "").strip()
    auth = _header(headers, "authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not profile or not token:
        raise AccessDenied("missing profile or token")
    if len(token) < MIN_TOKEN_LEN:
        raise AccessDenied("malformed token")
    row = conn.execute(
        "SELECT * FROM runners WHERE hermes_profile=? AND active=1", (profile,)
    ).fetchone()
    if row is None:
        raise AccessDenied("unknown profile or bad token")
    # Constant-time on both fields: a mismatch must not reveal which one was wrong.
    profile_ok = hmac.compare_digest(str(row["hermes_profile"]), profile)
    token_ok = hmac.compare_digest(str(row["mcp_token_hash"]), hash_token(token))
    if not (profile_ok and token_ok):
        raise AccessDenied("unknown profile or bad token")
    return row

