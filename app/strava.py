"""Direct Strava API client: OAuth, token refresh, activities, streams.

Tokens are always sent in the Authorization header (required from 2027-06-01).
"""
import time
from urllib.parse import urlencode

import httpx

from .config import settings

SCOPES = "read,activity:read_all"


def authorize_url(state: str) -> str:
    q = urlencode({
        "client_id": settings.strava_client_id,
        "redirect_uri": f"{settings.public_base_url}/strava/callback",
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPES,
        "state": state,
    })
    return f"{settings.strava_oauth_base}/authorize?{q}"


def _token_request(data: dict) -> dict:
    r = httpx.post(f"{settings.strava_oauth_base}/token", data={
        "client_id": settings.strava_client_id,
        "client_secret": settings.strava_client_secret,
        **data,
    }, timeout=20)
    r.raise_for_status()
    return r.json()


def exchange_code(code: str) -> dict:
    return _token_request({"code": code, "grant_type": "authorization_code"})


def save_tokens(conn, runner_id: int, tok: dict) -> None:
    conn.execute(
        "UPDATE runners SET strava_access_token=?, strava_refresh_token=?, strava_expires_at=?"
        " WHERE runner_id=?",
        (tok["access_token"], tok["refresh_token"], tok["expires_at"], runner_id),
    )
    conn.commit()


def access_token(conn, runner_id: int) -> str:
    """Return a valid access token, refreshing it if it expires within 5 minutes."""
    r = conn.execute(
        "SELECT strava_access_token, strava_refresh_token, strava_expires_at FROM runners WHERE runner_id=?",
        (runner_id,),
    ).fetchone()
    if not r or not r["strava_refresh_token"]:
        raise RuntimeError(f"runner {runner_id} has not connected Strava")
    if (r["strava_expires_at"] or 0) - 300 > time.time():
        return r["strava_access_token"]
    tok = _token_request({"grant_type": "refresh_token", "refresh_token": r["strava_refresh_token"]})
    save_tokens(conn, runner_id, tok)
    return tok["access_token"]


def _get(token: str, path: str, params: dict | None = None):
    r = httpx.get(f"{settings.strava_api_base}{path}", params=params,
                  headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def get_activity(token: str, activity_id: int) -> dict:
    return _get(token, f"/activities/{activity_id}")


def get_streams(token: str, activity_id: int) -> dict[str, list]:
    data = _get(token, f"/activities/{activity_id}/streams",
                {"keys": "time,heartrate,velocity_smooth,distance", "key_by_type": "true"})
    return {k: v.get("data", []) for k, v in data.items()}


def revoke(token: str) -> None:
    """Disconnect a runner (oauth/deauthorize is retired 2027-06-01; use revoke after that)."""
    httpx.post(f"{settings.strava_oauth_base}/deauthorize",
               headers={"Authorization": f"Bearer {token}"}, timeout=20)
