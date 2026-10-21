"""Settings, read from environment variables (see .env.example)."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: _env("RAC_DB_PATH", "data/runners.db"))

    hermes_base_url: str = field(default_factory=lambda: _env("HERMES_BASE_URL", "http://localhost:8642"))
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    coach_chat_id: str = field(default_factory=lambda: _env("COACH_CHAT_ID"))

    # Header Hermes uses to send the calling profile name (mcp_servers.<name>.identity_header).
    mcp_profile_header: str = field(default_factory=lambda: _env("MCP_PROFILE_HEADER", "x-hermes-profile"))

    # Where Hermes saves files a runner sends to the bot. {profile} is the authenticated
    # profile name, so each runner can only submit files from their own folder.
    hermes_home: str = field(default_factory=lambda: _env("HERMES_HOME", ""))
    upload_root_template: str = field(
        default_factory=lambda: _env("UPLOAD_ROOT", "{hermes_home}/profiles/{profile}"))


settings = Settings()
