"""SQLite rules and durable notification queue for one bot process."""

import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from asset_alerts.formatting import TimestampFormatter, local_timestamp
from asset_alerts.models import Asset, Direction, Quote, matches, positive_price, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL,
    threshold TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    asset TEXT PRIMARY KEY,
    price TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL UNIQUE REFERENCES alerts(id),
    price TEXT NOT NULL,
    quote_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TEXT,
    message_id TEXT
);
CREATE TABLE IF NOT EXISTS health (
    asset TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    error TEXT
);
"""

HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS quote_history (
    asset TEXT NOT NULL,
    price TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (asset, updated_at)
);
CREATE TABLE IF NOT EXISTS briefings (
    local_date TEXT PRIMARY KEY,
    scheduled_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TEXT,
    message_id TEXT
);
CREATE TABLE IF NOT EXISTS briefing_schedule (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_at TEXT NOT NULL
);
"""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone.")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    # Fixed precision keeps SQLite text comparisons chronological, including exact seconds.
    return _utc(value).isoformat(timespec="microseconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError(f"Database schema version {version} is newer than supported (1).")
            if version < 1:
                self.db.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + SCHEMA
                    + HISTORY_SCHEMA
                    + "\nPRAGMA user_version=1;\nCOMMIT;"
                )
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def latest(self, asset: Asset) -> Quote | None:
        row = self.db.execute("SELECT * FROM quotes WHERE asset=?", (asset.value,)).fetchone()
        if row is None:
            return None
        return Quote(asset, Decimal(row["price"]), datetime.fromisoformat(row["updated_at"]))

    def history(self, asset: Asset, start: datetime, end: datetime) -> list[Quote]:
        rows = self.db.execute(
            """SELECT price, updated_at FROM quote_history
               WHERE asset=? AND updated_at>=? AND updated_at<=? ORDER BY updated_at""",
            (asset.value, _timestamp(start), _timestamp(end)),
        ).fetchall()
        return [
            Quote(asset, Decimal(row["price"]), datetime.fromisoformat(row["updated_at"]))
            for row in rows
        ]

    def history_bounds(self, asset: Asset) -> tuple[datetime | None, datetime | None, int]:
        row = self.db.execute(
            """SELECT MIN(updated_at) first, MAX(updated_at) last, COUNT(*) count
               FROM quote_history WHERE asset=?""",
            (asset.value,),
        ).fetchone()
        return (
            datetime.fromisoformat(row["first"]) if row["first"] else None,
            datetime.fromisoformat(row["last"]) if row["last"] else None,
            row["count"],
        )

    def active_alerts(self, asset: Asset) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM alerts WHERE asset=? AND state='active' ORDER BY id", (asset.value,)
        ).fetchall()

    def add(self, asset: Asset, direction: Direction, threshold: Decimal) -> int:
        price = positive_price(threshold)
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO alerts(asset, direction, threshold, created_at) VALUES (?, ?, ?, ?)",
                (asset.value, direction.value, str(price), utcnow().isoformat()),
            )
        return int(cursor.lastrowid)

    def alerts(self, limit: int = 20, offset: int = 0) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT a.*, d.status AS delivery_status FROM alerts a
               LEFT JOIN deliveries d ON d.alert_id=a.id
               WHERE a.state != 'removed' ORDER BY a.id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()

    def change_state(self, alert_id: int, action: str) -> bool:
        source, target = {"pause": ("active", "paused"), "resume": ("paused", "active")}[action]
        with self.db:
            cursor = self.db.execute(
                "UPDATE alerts SET state=? WHERE id=? AND state=?", (target, alert_id, source)
            )
        return cursor.rowcount == 1

    def remove(self, alert_id: int) -> bool:
        with self.db:
            cursor = self.db.execute(
                "UPDATE alerts SET state='removed' WHERE id=? AND state != 'removed'", (alert_id,)
            )
            self.db.execute(
                "UPDATE deliveries SET status='cancelled' WHERE alert_id=? AND status='pending'",
                (alert_id,),
            )
        return cursor.rowcount == 1

    def health(self, asset: Asset, now: datetime, error: str | None) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO health(asset, checked_at, error) VALUES (?, ?, ?)",
                (asset.value, _timestamp(now), error),
            )

    def record_and_evaluate(self, quote: Quote, now: datetime, max_age: int) -> None:
        now = _utc(now)
        try:
            quote = Quote(quote.asset, positive_price(quote.price), _utc(quote.updated_at))
        except (ValueError, TypeError, AttributeError) as exc:
            self.health(quote.asset, now, f"Invalid quote; alerts skipped: {exc}")
            return
        if not quote.is_fresh(now, max_age):
            self.health(quote.asset, now, "Stale or future-dated quote; alerts skipped.")
            return
        previous = self.db.execute(
            "SELECT updated_at FROM quotes WHERE asset=?", (quote.asset.value,)
        ).fetchone()
        if previous and quote.updated_at < datetime.fromisoformat(previous["updated_at"]):
            self.health(quote.asset, now, "Quote timestamp moved backwards; alerts skipped.")
            return
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO quotes VALUES (?, ?, ?, ?)",
                (
                    quote.asset.value,
                    str(quote.price),
                    _timestamp(quote.updated_at),
                    _timestamp(now),
                ),
            )
            self.db.execute(
                """INSERT OR IGNORE INTO quote_history
                   (asset, price, updated_at, collected_at, source) VALUES (?, ?, ?, ?, ?)""",
                (
                    quote.asset.value,
                    str(quote.price),
                    _timestamp(quote.updated_at),
                    _timestamp(now),
                    "gold-api.com",
                ),
            )
            rules = self.db.execute(
                "SELECT * FROM alerts WHERE asset=? AND state='active'", (quote.asset.value,)
            ).fetchall()
            for rule in rules:
                if matches(Direction(rule["direction"]), Decimal(rule["threshold"]), quote.price):
                    self.db.execute(
                        """INSERT INTO deliveries(alert_id, price, quote_time, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (
                            rule["id"],
                            str(quote.price),
                            _timestamp(quote.updated_at),
                            _timestamp(now),
                        ),
                    )
                    self.db.execute("UPDATE alerts SET state='triggered' WHERE id=?", (rule["id"],))
            self.db.execute(
                "INSERT OR REPLACE INTO health(asset, checked_at, error) VALUES (?, ?, NULL)",
                (quote.asset.value, _timestamp(now)),
            )

    def queue_briefing(
        self, local_date: str, scheduled_at: datetime, payload: str, now: datetime
    ) -> bool:
        with self.db:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO briefings
                   (local_date, scheduled_at, created_at, payload) VALUES (?, ?, ?, ?)""",
                (local_date, _timestamp(scheduled_at), _timestamp(now), payload),
            )
        return cursor.rowcount == 1

    def briefing_started_at(self, now: datetime) -> datetime:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO briefing_schedule(id, started_at) VALUES (1, ?)",
                (_timestamp(now),),
            )
            row = self.db.execute("SELECT started_at FROM briefing_schedule WHERE id=1").fetchone()
        return datetime.fromisoformat(row["started_at"])

    def briefing_exists(self, local_date: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM briefings WHERE local_date=?", (local_date,)).fetchone()
            is not None
        )

    def pending_briefings(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM briefings WHERE status='pending' ORDER BY scheduled_at, local_date"
        ).fetchall()

    def briefing_delivered(self, local_date: str, message_id: int, now: datetime) -> None:
        with self.db:
            self.db.execute(
                """UPDATE briefings SET status='sent', sent_at=?, message_id=?,
                   attempts=attempts+1, last_error=NULL WHERE local_date=? AND status='pending'""",
                (_timestamp(now), str(message_id), local_date),
            )

    def briefing_failed(self, local_date: str, error: str) -> None:
        with self.db:
            self.db.execute(
                """UPDATE briefings SET attempts=attempts+1, last_error=?
                   WHERE local_date=? AND status='pending'""",
                (error, local_date),
            )

    def backup(self, output: Path) -> Path:
        """Publish a verified, self-contained snapshot without replacing an existing file."""
        output = output.absolute()
        if os.path.lexists(output):
            raise FileExistsError(f"Backup destination already exists: {output}")
        if self.db.in_transaction:
            raise ValueError("Cannot back up during an uncommitted transaction.")
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            target = sqlite3.connect(temporary)
            try:
                self.db.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
                results = target.execute("PRAGMA integrity_check").fetchall()
                if results != [("ok",)]:
                    raise ValueError("Backup integrity check failed.")
            finally:
                target.close()
            # The hard link publishes atomically and fails even if a destination was
            # created while the snapshot was being written. Both paths share a filesystem.
            os.link(temporary, output)
            return output
        finally:
            temporary.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)

    def pending(self) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT d.*, a.asset, a.direction, a.threshold FROM deliveries d
               JOIN alerts a ON a.id=d.alert_id WHERE d.status='pending' ORDER BY d.id"""
        ).fetchall()

    def delivered(self, delivery_id: int, message_id: int) -> None:
        with self.db:
            self.db.execute(
                """UPDATE deliveries SET status='sent', sent_at=?, message_id=?,
                   attempts=attempts+1, last_error=NULL WHERE id=?""",
                (utcnow().isoformat(), str(message_id), delivery_id),
            )

    def failed(self, delivery_id: int, error: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE deliveries SET attempts=attempts+1, last_error=? WHERE id=?",
                (error, delivery_id),
            )

    def status_text(self, *, format_time: TimestampFormatter = local_timestamp) -> str:
        lines = []
        for asset in Asset:
            health = self.db.execute(
                "SELECT * FROM health WHERE asset=?", (asset.value,)
            ).fetchone()
            quote = self.db.execute("SELECT * FROM quotes WHERE asset=?", (asset.value,)).fetchone()
            if health:
                lines.append(f"{asset.value}: checked {format_time(health['checked_at'])}")
                if health["error"]:
                    lines.append(f"  Problem: {health['error']}")
            else:
                lines.append(f"{asset.value}: not checked yet")
            if quote:
                lines.append(
                    f"  Last valid quote: ${quote['price']} at {format_time(quote['updated_at'])}"
                )
            first, last, count = self.history_bounds(asset)
            if count:
                lines.append(
                    f"  History: {count} observations, "
                    f"{format_time(first.isoformat())} to {format_time(last.isoformat())}"
                )
            else:
                lines.append("  History: no observations yet")
        counts = self.db.execute("SELECT state, COUNT(*) n FROM alerts GROUP BY state").fetchall()
        lines.append("Rules: " + (", ".join(f"{r['state']}={r['n']}" for r in counts) or "none"))
        pending = self.pending()
        lines.append(f"Pending deliveries: {len(pending)}")
        for delivery in pending[:3]:
            if delivery["last_error"]:
                lines.append(f"  Alert #{delivery['alert_id']}: {delivery['last_error']}")
        briefings = self.pending_briefings()
        lines.append(f"Pending briefings: {len(briefings)}")
        for briefing in briefings[:3]:
            if briefing["last_error"]:
                lines.append(f"  Briefing {briefing['local_date']}: {briefing['last_error']}")
        return "\n".join(lines)
