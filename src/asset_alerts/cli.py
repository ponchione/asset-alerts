"""Local diagnostics and the bot entry point."""

import argparse
import asyncio
import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path

from asset_alerts.config import Settings
from asset_alerts.models import Asset, utcnow
from asset_alerts.monitor import price_text
from asset_alerts.storage import Store


@contextmanager
def instance_lock(database_path: Path):
    """Keep two desktop services from sending the same queued alert."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with database_path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("A bot is already running against this database.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


async def fetch_prices(settings: Settings) -> int:
    import aiohttp

    from asset_alerts.provider import GoldAPI

    failed = False
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        provider = GoldAPI(session)
        for asset in Asset:
            try:
                quote = await provider.fetch(asset)
                print(price_text(asset, quote, settings.max_quote_age_seconds, utcnow()))
                failed |= not quote.is_fresh(utcnow(), settings.max_quote_age_seconds)
            except Exception as exc:
                print(f"{asset.value}: fetch failed ({type(exc).__name__})")
                failed = True
    return int(failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Personal Discord price alerts")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Run the Discord bot and price monitor")
    commands.add_parser("prices", help="Fetch prices without Discord or creating alerts")
    commands.add_parser("status", help="Read saved check and delivery status")
    commands.add_parser("doctor", help="Validate local configuration without connecting")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        if args.env_file.is_file():
            from dotenv import load_dotenv

            load_dotenv(args.env_file, override=False)
        settings = Settings.from_env()
        if args.command == "doctor":
            missing = settings.missing_discord_settings()
            print(f"Database: {settings.database_path.resolve()}")
            print(
                f"Polling: {settings.poll_seconds}s; "
                f"max quote age: {settings.max_quote_age_seconds}s"
            )
            print(
                "Missing: " + ", ".join(missing)
                if missing
                else "Discord configuration is complete."
            )
            print("Local check only; credentials and channel access have not been verified.")
            return int(bool(missing))
        if args.command == "prices":
            return asyncio.run(fetch_prices(settings))
        if args.command == "run":
            settings.require_discord()
            from asset_alerts.bot import PriceBot

            with instance_lock(settings.database_path):
                store = Store(settings.database_path)
                try:
                    PriceBot(settings, store).run(settings.token, log_handler=None)
                finally:
                    store.close()
            return 0
        if not settings.database_path.is_file():
            print("No saved state yet. Start the bot with: asset-alerts run")
            return 0
        store = Store(settings.database_path)
        try:
            print(store.status_text())
        finally:
            store.close()
        return 0
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2
    except ImportError:
        print("Dependencies are missing. Run uv sync from the project directory.")
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # Never print exception bodies that might contain the bot token.
        print(f"Operation failed ({type(exc).__name__}). Check configuration and connectivity.")
        return 1
