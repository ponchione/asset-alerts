"""Environment configuration; secrets are never included in diagnostic output."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Settings:
    token: str = field(default="", repr=False)
    owner_id: int = 0
    guild_id: int = 0
    channel_id: int = 0
    poll_seconds: int = 300
    max_quote_age_seconds: int = 900
    database_path: Path = Path("data/alerts.sqlite3")
    briefing_enabled: bool = False
    briefing_time: str = "08:00"
    briefing_timezone: str = "America/New_York"

    @classmethod
    def from_env(cls) -> "Settings":
        def number(name: str, default: int, minimum: int) -> int:
            try:
                value = int(os.environ.get(name, "").strip() or default)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer.") from exc
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}.")
            return value

        enabled = os.environ.get("BRIEFING_ENABLED", "false").strip().lower()
        if enabled not in {"true", "false"}:
            raise ValueError("BRIEFING_ENABLED must be true or false.")
        briefing_time = os.environ.get("BRIEFING_TIME", "08:00").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", briefing_time):
            raise ValueError("BRIEFING_TIME must be HH:MM in 24-hour time.")
        timezone = os.environ.get("BRIEFING_TIMEZONE", "America/New_York").strip()
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                "BRIEFING_TIMEZONE must be an IANA timezone, e.g. America/New_York."
            ) from exc

        return cls(
            token=os.environ.get("DISCORD_TOKEN", "").strip(),
            owner_id=number("DISCORD_OWNER_ID", 0, 0),
            guild_id=number("DISCORD_GUILD_ID", 0, 0),
            channel_id=number("DISCORD_CHANNEL_ID", 0, 0),
            poll_seconds=number("POLL_SECONDS", 300, 30),
            max_quote_age_seconds=number("MAX_QUOTE_AGE_SECONDS", 900, 30),
            database_path=Path(os.environ.get("DATABASE_PATH") or "data/alerts.sqlite3"),
            briefing_enabled=enabled == "true",
            briefing_time=briefing_time,
            briefing_timezone=timezone,
        )

    def missing_discord_settings(self) -> list[str]:
        return [
            name
            for name, value in (
                ("DISCORD_TOKEN", self.token),
                ("DISCORD_OWNER_ID", self.owner_id),
                ("DISCORD_GUILD_ID", self.guild_id),
                ("DISCORD_CHANNEL_ID", self.channel_id),
            )
            if not value
        ]

    def require_discord(self) -> None:
        missing = self.missing_discord_settings()
        if missing:
            raise ValueError("Set these in .env first: " + ", ".join(missing))
