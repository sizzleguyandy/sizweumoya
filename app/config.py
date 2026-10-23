"""Settings, read from environment variables (see .env.example)."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: _env("RAC_DB_PATH", "data/runners.db"))
    # One complete .db file per runner (a copy of everything held for them), kept in sync.
    records_dir: str = field(default_factory=lambda: _env("RAC_RECORDS_DIR", "data/runners"))

    hermes_base_url: str = field(default_factory=lambda: _env("HERMES_BASE_URL", "http://localhost:8642"))
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    coach_chat_id: str = field(default_factory=lambda: _env("COACH_CHAT_ID"))

    # Header Hermes uses to send the calling profile name (mcp_servers.<name>.identity_header).
    mcp_profile_header: str = field(default_factory=lambda: _env("MCP_PROFILE_HEADER", "x-hermes-profile"))

    # Where Hermes saves files a runner sends to the bot. {profile} is the authenticated
    # profile name, so each runner can only submit files from their own folder.
    # Garmin Connect Developer Program (Starter: push only). The client ID is checked on every
    # push; the secret stays in .env for the OAuth step and is never logged.
    garmin_client_id: str = field(default_factory=lambda: _env("GARMIN_CLIENT_ID"))
    # Until Garmin confirms in writing that AI feedback is allowed (agreement 15.11), activities
    # from Garmin are stored but Sarah is not asked to comment on them automatically.
    garmin_ai_allowed: bool = field(default_factory=lambda: _env("GARMIN_AI_ALLOWED", "0") == "1")

    hevy_base_url: str = field(default_factory=lambda: _env("HEVY_BASE_URL", "https://api.hevyapp.com"))

    hermes_home: str = field(default_factory=lambda: _env("HERMES_HOME", ""))
    upload_root_template: str = field(
        default_factory=lambda: _env("UPLOAD_ROOT", "{hermes_home}/profiles/{profile}"))


settings = Settings()
