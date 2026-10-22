import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from asset_alerts.models import Asset, Direction, Quote
from asset_alerts.monitor import Monitor
from asset_alerts.storage import SCHEMA, Store

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "alerts.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, price="3100", when=NOW, now=NOW, asset=Asset.GOLD):
        self.store.record_and_evaluate(Quote(asset, Decimal(price), when), now, 900)

    def test_history_survives_restart_and_preserves_decimal_precision(self):
        self.record(price="3100.1234567890123456789")
        self.record(price="3200", when=NOW + timedelta(minutes=5), now=NOW + timedelta(minutes=5))
        self.record(price="40", asset=Asset.SILVER)
        self.store.close()
        self.store = Store(self.path)
        history = self.store.history(Asset.GOLD, NOW, NOW + timedelta(minutes=5))
        self.assertEqual([q.price for q in history], [Decimal("3100.1234567890123456789"), 3200])
        self.assertEqual([q.updated_at for q in history], [NOW, NOW + timedelta(minutes=5)])
        self.assertEqual(
            self.store.history_bounds(Asset.GOLD), (NOW, NOW + timedelta(minutes=5), 2)
        )
        self.assertEqual(self.store.history_bounds(Asset.BITCOIN), (None, None, 0))
        row = self.store.db.execute("SELECT * FROM quote_history ORDER BY updated_at").fetchone()
        self.assertEqual(datetime.fromisoformat(row["collected_at"]), NOW)
        self.assertEqual(row["source"], "gold-api.com")

    def test_duplicate_provider_instant_keeps_original_history_and_evaluates_new_rule(self):
        self.record()
        alert_id = self.store.add(Asset.GOLD, Direction.BELOW, Decimal("3000"))
        eastern = NOW.astimezone(timezone(timedelta(hours=-4)))
        self.record(price="2999", when=eastern)
        self.assertEqual(self.store.history_bounds(Asset.GOLD), (NOW, NOW, 1))
        self.assertEqual(self.store.history(Asset.GOLD, NOW, NOW)[0].price, Decimal("3100"))
        self.assertEqual(self.store.latest(Asset.GOLD).price, Decimal("2999"))
        self.assertEqual(self.store.pending()[0]["alert_id"], alert_id)

    def test_history_ranges_are_inclusive_and_normalized_to_utc(self):
        eastern = timezone(timedelta(hours=-4))
        second = NOW + timedelta(microseconds=1)
        self.record(when=NOW.astimezone(eastern))
        self.record(price="3200", when=second, now=second)
        self.assertEqual(len(self.store.history(Asset.GOLD, NOW, NOW)), 1)
        points = self.store.history(Asset.GOLD, NOW.astimezone(eastern), second)
        self.assertEqual([q.updated_at for q in points], [NOW, second])
        self.assertTrue(all(q.updated_at.tzinfo == UTC for q in points))
        with self.assertRaises(ValueError):
            self.store.history(Asset.GOLD, NOW.replace(tzinfo=None), NOW)

    def test_rejected_quotes_never_enter_history_or_trigger_alerts(self):
        self.record()
        self.store.add(Asset.GOLD, Direction.BELOW, Decimal("3000"))
        for price, when in (
            ("2999", NOW - timedelta(seconds=901)),
            ("2999", NOW + timedelta(seconds=61)),
            ("2999", NOW - timedelta(seconds=1)),
            ("NaN", NOW),
            ("sNaN", NOW),
            ("Infinity", NOW),
            ("-1", NOW),
            ("0", NOW),
            ("1e99", NOW),
            ("2999", NOW.replace(tzinfo=None)),
        ):
            with self.subTest(price=price, when=when):
                self.record(price, when)
                self.assertEqual(self.store.history_bounds(Asset.GOLD)[2], 1)
                self.assertEqual(self.store.pending(), [])
                self.assertEqual(self.store.latest(Asset.GOLD).price, Decimal("3100"))

    def test_history_quote_rule_and_delivery_roll_back_together(self):
        self.store.add(Asset.GOLD, Direction.BELOW, Decimal("3200"))
        self.store.db.execute(
            """CREATE TRIGGER reject_delivery BEFORE INSERT ON deliveries
               BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.record()
        self.assertEqual(self.store.history_bounds(Asset.GOLD)[2], 0)
        self.assertIsNone(self.store.latest(Asset.GOLD))
        self.assertEqual(self.store.alerts()[0]["state"], "active")
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM health").fetchone()[0], 0)

    def test_active_alerts_excludes_other_assets_and_inactive_rules(self):
        wanted = self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("3500"))
        paused = self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("3600"))
        removed = self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("3700"))
        self.store.add(Asset.SILVER, Direction.ABOVE, Decimal("40"))
        self.store.change_state(paused, "pause")
        self.store.remove(removed)
        self.assertEqual([row["id"] for row in self.store.active_alerts(Asset.GOLD)], [wanted])

    def test_status_includes_history_and_failed_pending_briefing(self):
        self.record()
        self.store.queue_briefing("2026-10-03", NOW, "{}", NOW)
        self.store.briefing_failed("2026-10-03", "Discord unavailable")
        status = self.store.status_text()
        self.assertIn("History: 1 observations", status)
        self.assertIn("History: no observations yet", status)
        self.assertIn("Pending briefings: 1", status)
        self.assertIn("Briefing 2026-10-03: Discord unavailable", status)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(SCHEMA)
            db.execute(
                """INSERT INTO alerts(id, asset, direction, threshold, state, created_at)
                   VALUES (42, 'gold', 'below', '3000', 'triggered', ?)""",
                (NOW.isoformat(),),
            )
            db.execute(
                "INSERT INTO quotes VALUES ('gold', '2999.123456789', ?, ?)",
                (NOW.isoformat(), NOW.isoformat()),
            )
            db.execute(
                """INSERT INTO deliveries(alert_id, price, quote_time, created_at,
                   attempts, last_error) VALUES (42, '2999.123456789', ?, ?, 2, 'offline')""",
                (NOW.isoformat(), NOW.isoformat()),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_additive_migration_preserves_pending_and_does_not_backfill_history(self):
        for _ in range(2):
            store = Store(self.path)
            try:
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(store.latest(Asset.GOLD).price, Decimal("2999.123456789"))
                self.assertEqual(store.history_bounds(Asset.GOLD), (None, None, 0))
                self.assertEqual(store.alerts()[0]["id"], 42)
                pending = store.pending()
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["attempts"], 2)
                self.assertEqual(pending[0]["last_error"], "offline")
            finally:
                store.close()

    def test_failed_migration_rolls_back_schema_and_retains_pending(self):
        with patch(
            "asset_alerts.storage.HISTORY_SCHEMA", "CREATE TABLE partial (id); INVALID SQL;"
        ):
            with self.assertRaises(sqlite3.OperationalError):
                Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertIsNone(
                db.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone()
            )
            self.assertEqual(db.execute("SELECT attempts FROM deliveries").fetchone()[0], 2)

    def test_future_schema_is_rejected_without_downgrade(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA user_version=999")
        with self.assertRaisesRegex(ValueError, "newer than supported"):
            Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 999)


class BriefingOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "alerts.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_multiple_pending_dates_survive_restart_and_retries_preserve_payload(self):
        for offset in (1, 0):
            when = NOW + timedelta(days=offset)
            self.assertTrue(
                self.store.queue_briefing(when.date().isoformat(), when, '{"v":1}', when)
            )
        self.store.briefing_failed("2026-10-03", "offline")
        self.store.close()
        self.store = Store(self.path)
        self.assertFalse(self.store.queue_briefing("2026-10-03", NOW, '{"v":2}', NOW))
        pending = self.store.pending_briefings()
        self.assertEqual([row["local_date"] for row in pending], ["2026-10-03", "2026-10-04"])
        self.assertEqual(pending[0]["payload"], '{"v":1}')
        self.assertEqual(pending[0]["attempts"], 1)
        self.assertEqual(pending[0]["last_error"], "offline")
        self.assertTrue(self.store.briefing_exists("2026-10-03"))
        self.assertFalse(self.store.briefing_exists("2026-10-05"))
        self.store.briefing_delivered("2026-10-03", 123, NOW)
        self.store.briefing_delivered("2026-10-03", 456, NOW)
        self.store.briefing_failed("2026-10-03", "should not modify sent row")
        row = self.store.db.execute(
            "SELECT * FROM briefings WHERE local_date='2026-10-03'"
        ).fetchone()
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["message_id"], "123")
        self.assertIsNone(row["last_error"])
        self.assertEqual(datetime.fromisoformat(row["sent_at"]), NOW)
        self.assertEqual(len(self.store.pending_briefings()), 1)
        self.assertTrue(self.store.briefing_exists("2026-10-03"))
        self.assertFalse(self.store.queue_briefing("2026-10-03", NOW, "{}", NOW))

    def test_schedule_activation_is_stable_across_restart(self):
        eastern_now = NOW.astimezone(timezone(timedelta(hours=-4)))
        self.assertEqual(self.store.briefing_started_at(eastern_now), NOW)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.briefing_started_at(NOW + timedelta(days=3)), NOW)


class BackupTests(unittest.TestCase):
    def test_live_wal_backup_is_independent_and_keeps_all_durable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.sqlite3"
            output = Path(directory) / "backup.sqlite3"
            store = Store(path)
            try:
                store.add(Asset.GOLD, Direction.BELOW, Decimal("3200"))
                store.record_and_evaluate(Quote(Asset.GOLD, Decimal("3100"), NOW), NOW, 900)
                store.queue_briefing("2026-10-03", NOW, '{"body":"Morning"}', NOW)
                store.briefing_started_at(NOW)
                self.assertTrue(Path(str(path) + "-wal").exists())
                self.assertEqual(store.backup(output), output)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertFalse(Path(str(output) + "-wal").exists())
                self.assertFalse(Path(str(output) + "-shm").exists())
                with tempfile.TemporaryDirectory() as independent:
                    copy = Path(independent) / "restored.sqlite3"
                    shutil.copyfile(output, copy)
                    with closing(sqlite3.connect(copy)) as db:
                        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                        self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                        for table in (
                            "alerts",
                            "deliveries",
                            "quote_history",
                            "health",
                            "briefings",
                        ):
                            self.assertEqual(
                                db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1
                            )
                        self.assertEqual(
                            db.execute("SELECT status FROM deliveries").fetchone()[0], "pending"
                        )
                        self.assertEqual(
                            db.execute("SELECT status FROM briefings").fetchone()[0], "pending"
                        )
                with self.assertRaises(FileExistsError):
                    store.backup(output)
                self.assertEqual(list(Path(directory).glob(".backup.sqlite3.*")), [])
            finally:
                store.close()

    def test_backup_does_not_overwrite_arbitrary_existing_file_or_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "live.sqlite3")
            output = Path(directory) / "existing.txt"
            output.write_text("preserve me")
            link = Path(directory) / "dangling.sqlite3"
            link.symlink_to(Path(directory) / "absent")
            try:
                for destination in (output, link):
                    with self.assertRaises(FileExistsError):
                        store.backup(destination)
                self.assertEqual(output.read_text(), "preserve me")
                self.assertTrue(link.is_symlink())
            finally:
                store.close()

    def test_failed_backup_cleans_up_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "live.sqlite3")
            output = Path(directory) / "backup.sqlite3"
            try:
                with patch("asset_alerts.storage.os.link", side_effect=OSError("injected failure")):
                    with self.assertRaises(OSError):
                        store.backup(output)
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(directory).glob(".backup.sqlite3.*")), [])
            finally:
                store.close()


class HistoryMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_feed_failure_does_not_prevent_history_for_other_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "alerts.sqlite3")
            provider = AsyncMock()

            async def fetch(asset):
                if asset == Asset.GOLD:
                    raise TimeoutError()
                return Quote(asset, Decimal("30"), datetime.now(UTC))

            provider.fetch.side_effect = fetch
            try:
                await Monitor(store, provider, AsyncMock()).check_prices()
                self.assertEqual(store.history_bounds(Asset.GOLD)[2], 0)
                self.assertEqual(store.history_bounds(Asset.SILVER)[2], 1)
                self.assertEqual(store.history_bounds(Asset.BITCOIN)[2], 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
