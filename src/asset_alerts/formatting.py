"""Readable timestamps for terminal output and Discord messages."""

from collections.abc import Callable
from datetime import datetime

type TimestampFormatter = Callable[[datetime | str], str]


def _datetime(value: datetime | str) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.utcoffset() is None:
        raise ValueError("Display timestamps must include a timezone.")
    return result


def local_timestamp(value: datetime | str) -> str:
    return _datetime(value).astimezone().strftime("%b %-d, %Y at %-I:%M %p %Z")


def discord_timestamp(value: datetime | str) -> str:
    # Discord renders both parts using the reader's timezone and locale, and
    # keeps the relative age current as the message gets older.
    seconds = int(_datetime(value).timestamp())
    return f"<t:{seconds}:f> (<t:{seconds}:R>)"
