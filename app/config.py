"""Settings, read from environment variables (see .env.example)."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: _env("RAC_DB_PATH", "data/runners.db"))

    strava_client_id: str = field(default_factory=lambda: _env("STRAVA_CLIENT_ID"))
    strava_client_secret: str = field(default_factory=lambda: _env("STRAVA_CLIENT_SECRET"))
    strava_verify_token: str = field(default_factory=lambda: _env("STRAVA_VERIFY_TOKEN"))
    # Strava moves to https://www.api-v3.strava.com on 2027-06-01; switch this then.
    strava_api_base: str = field(default_factory=lambda: _env("STRAVA_API_BASE", "https://www.strava.com/api/v3"))
    strava_oauth_base: str = field(default_factory=lambda: _env("STRAVA_OAUTH_BASE", "https://www.strava.com/oauth"))
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8000"))

    hermes_base_url: str = field(default_factory=lambda: _env("HERMES_BASE_URL", "http://localhost:8642"))
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    coach_chat_id: str = field(default_factory=lambda: _env("COACH_CHAT_ID"))

    # Header Hermes uses to send the calling profile name (mcp_servers.<name>.identity_header).
    mcp_profile_header: str = field(default_factory=lambda: _env("MCP_PROFILE_HEADER", "x-hermes-profile"))


settings = Settings()
