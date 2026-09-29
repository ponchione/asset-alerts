# Project conventions

- Personal, single-user Discord bot for gold, silver, and bitcoin, running on Linux.
- Python 3.12+, `uv`, SQLite. Keep the domain and persistence independent of Discord.
- Use `Decimal` for prices and timezone-aware UTC timestamps.
- Every slash command must enforce the configured owner, guild, and channel.
- Do not commit `.env`, tokens, local databases, or runtime files.
- Queue alerts transactionally; preserve pending deliveries across restarts.
- Never evaluate stale, invalid, or out-of-order quotes.
- Run `uv run pytest` and `uv run ruff check .` for functional changes.
- Core tests also run offline: `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
- Live Discord checks require explicitly configured credentials and a test channel.
