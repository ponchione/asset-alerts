"""SQLite rules and durable notification queue for one bot process."""

import sqlite3
from datetime import datetime
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


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def latest(self, asset: Asset) -> Quote | None:
        row = self.db.execute("SELECT * FROM quotes WHERE asset=?", (asset.value,)).fetchone()
        if row is None:
            return None
        return Quote(asset, Decimal(row["price"]), datetime.fromisoformat(row["updated_at"]))

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
                (asset.value, now.isoformat(), error),
            )

    def record_and_evaluate(self, quote: Quote, now: datetime, max_age: int) -> None:
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
                    quote.updated_at.isoformat(),
                    now.isoformat(),
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
                            quote.updated_at.isoformat(),
                            now.isoformat(),
                        ),
                    )
                    self.db.execute("UPDATE alerts SET state='triggered' WHERE id=?", (rule["id"],))
        self.health(quote.asset, now, None)

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
        counts = self.db.execute("SELECT state, COUNT(*) n FROM alerts GROUP BY state").fetchall()
        lines.append("Rules: " + (", ".join(f"{r['state']}={r['n']}" for r in counts) or "none"))
        pending = self.pending()
        lines.append(f"Pending deliveries: {len(pending)}")
        for delivery in pending[:3]:
            if delivery["last_error"]:
                lines.append(f"  Alert #{delivery['alert_id']}: {delivery['last_error']}")
        return "\n".join(lines)
