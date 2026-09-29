"""Environment configuration; secrets are never included in diagnostic output."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    token: str = field(default="", repr=False)
    owner_id: int = 0
    guild_id: int = 0
    channel_id: int = 0
    poll_seconds: int = 300
    max_quote_age_seconds: int = 900
    database_path: Path = Path("data/alerts.sqlite3")

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

        return cls(
            token=os.environ.get("DISCORD_TOKEN", "").strip(),
            owner_id=number("DISCORD_OWNER_ID", 0, 0),
            guild_id=number("DISCORD_GUILD_ID", 0, 0),
            channel_id=number("DISCORD_CHANNEL_ID", 0, 0),
            poll_seconds=number("POLL_SECONDS", 300, 30),
            max_quote_age_seconds=number("MAX_QUOTE_AGE_SECONDS", 900, 30),
            database_path=Path(os.environ.get("DATABASE_PATH") or "data/alerts.sqlite3"),
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
