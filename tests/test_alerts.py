import asyncio
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from asset_alerts.cli import instance_lock, main
from asset_alerts.config import Settings
from asset_alerts.models import Asset, Direction, Quote, matches, positive_price
from asset_alerts.monitor import Monitor, alert_message
from asset_alerts.provider import GoldAPI, QuoteError, parse_quote
from asset_alerts.storage import Store

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


def quote(asset=Asset.GOLD, price="3000", when=NOW):
    return Quote(asset, Decimal(price), when)


class PriceTests(unittest.TestCase):
    def test_thresholds_are_inclusive_and_directional(self):
        for direction in Direction:
            self.assertTrue(matches(direction, Decimal("3000"), Decimal("3000")))
        self.assertTrue(matches(Direction.BELOW, Decimal("3000"), Decimal("2999.999")))
        self.assertFalse(matches(Direction.ABOVE, Decimal("3000"), Decimal("2999.999")))
        self.assertFalse(matches(Direction.BELOW, Decimal("3000"), Decimal("3000.001")))

    def test_invalid_prices_are_rejected(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity", "0", "-1", "oops", True, "1e99"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                positive_price(value)

    def test_json_quote_preserves_precision(self):
        result = parse_quote(
            Asset.GOLD,
            {
                "symbol": "XAU",
                "price": Decimal("3000.123456789"),
                "updatedAt": "2026-09-29T12:00:00Z",
                "currency": "USD",
            },
        )
        self.assertEqual(result.price, Decimal("3000.123456789"))
        self.assertEqual(result.updated_at, NOW)

    def test_invalid_quote_shapes_are_rejected(self):
        valid = {"symbol": "XAU", "price": 3000, "updatedAt": NOW.isoformat()}
        for payload in (
            None,
            [],
            {},
            {**valid, "symbol": "BTC"},
            {**valid, "currency": "EUR"},
            {**valid, "price": -1},
            {**valid, "updatedAt": "2026-09-29T12:00:00"},
            {**valid, "updatedAt": "broken"},
            {**valid, "updatedAt": None},
        ):
            with self.subTest(payload=payload), self.assertRaises(QuoteError):
                parse_quote(Asset.GOLD, payload)

    def test_quote_age_and_future_clock_tolerance(self):
        self.assertTrue(quote(when=NOW - timedelta(seconds=900)).is_fresh(NOW, 900))
        self.assertFalse(quote(when=NOW - timedelta(seconds=901)).is_fresh(NOW, 900))
        self.assertFalse(quote(when=NOW + timedelta(seconds=61)).is_fresh(NOW, 900))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "alerts.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, asset=Asset.GOLD, direction=Direction.BELOW, threshold="3000"):
        return self.store.add(asset, direction, Decimal(threshold))

    def check(self, value=None):
        self.store.record_and_evaluate(value or quote(), NOW, 900)

    def test_first_qualifying_quote_triggers_once_across_restart(self):
        alert_id = self.add()
        self.check(quote(price="2990"))
        self.store.close()
        self.store = Store(self.path)
        self.check(quote(price="2980"))
        self.assertEqual(len(self.store.pending()), 1)
        self.assertEqual(self.store.pending()[0]["alert_id"], alert_id)
        self.assertEqual(self.store.pending()[0]["price"], "2990")
        self.assertEqual(self.store.alerts()[0]["state"], "triggered")

    def test_crossing_is_detected_after_nonmatching_quote(self):
        self.add()
        self.check(quote(price="3100"))
        self.assertEqual(self.store.pending(), [])
        self.check(quote(price="3000"))
        self.assertEqual(len(self.store.pending()), 1)

    def test_pause_resume_and_invalid_transitions(self):
        alert_id = self.add()
        self.assertTrue(self.store.change_state(alert_id, "pause"))
        self.assertFalse(self.store.change_state(alert_id, "pause"))
        self.check()
        self.assertEqual(self.store.pending(), [])
        self.assertTrue(self.store.change_state(alert_id, "resume"))
        self.check()
        self.assertEqual(len(self.store.pending()), 1)
        self.assertFalse(self.store.change_state(alert_id, "resume"))

    def test_remove_cancels_pending_delivery_but_preserves_history(self):
        alert_id = self.add()
        self.check()
        self.assertTrue(self.store.remove(alert_id))
        self.assertFalse(self.store.remove(alert_id))
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.alerts(), [])
        self.assertEqual(
            self.store.db.execute("SELECT status FROM deliveries").fetchone()[0], "cancelled"
        )

    def test_stale_or_future_quote_never_triggers(self):
        self.add()
        for offset in (-901, 61):
            self.check(quote(when=NOW + timedelta(seconds=offset)))
        self.assertEqual(self.store.pending(), [])
        self.assertIsNone(self.store.latest(Asset.GOLD))
        self.assertIn("Stale or future-dated", self.store.status_text())

    def test_out_of_order_quote_does_not_trigger_or_replace_latest(self):
        self.add()
        self.check(quote(price="3100"))
        self.check(quote(price="2900", when=NOW - timedelta(seconds=1)))
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.latest(Asset.GOLD).price, Decimal("3100"))
        self.assertIn("backwards", self.store.status_text())

    def test_one_asset_does_not_trigger_another(self):
        self.add(asset=Asset.SILVER)
        self.check()
        self.assertEqual(self.store.pending(), [])

    def test_failed_delivery_remains_pending_until_confirmed(self):
        self.add()
        self.check()
        delivery_id = self.store.pending()[0]["id"]
        self.store.failed(delivery_id, "Discord unavailable")
        self.assertEqual(self.store.pending()[0]["attempts"], 1)
        self.assertIn("Discord unavailable", self.store.status_text())
        self.store.delivered(delivery_id, 123)
        self.check()
        self.assertEqual(self.store.pending(), [])
        row = self.store.db.execute("SELECT * FROM deliveries").fetchone()
        self.assertEqual(row["message_id"], "123")
        self.assertEqual(row["attempts"], 2)
        self.assertIsNone(row["last_error"])

    def test_rule_and_delivery_are_committed_atomically(self):
        self.add()
        self.store.db.execute("""CREATE TRIGGER prevent_delivery BEFORE INSERT ON deliveries
                                 BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.check()
        self.assertEqual(self.store.alerts()[0]["state"], "active")
        self.assertEqual(self.store.pending(), [])

    def test_delivery_message_has_original_timestamp_and_rule(self):
        self.add()
        self.check()
        message = alert_message(self.store.pending()[0], 42)
        self.assertIn("<@42>", message)
        self.assertIn(f"<t:{int(NOW.timestamp())}:f>", message)
        self.assertIn("Alert #1", message)


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "alerts.sqlite3"
        self.store = Store(self.path)

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_failed_feed_does_not_block_other_assets(self):
        self.store.add(Asset.SILVER, Direction.ABOVE, Decimal("20"))
        provider = AsyncMock()

        async def fetch(asset):
            if asset == Asset.GOLD:
                raise TimeoutError()
            return quote(asset, "30", datetime.now(UTC))

        provider.fetch.side_effect = fetch
        notify = AsyncMock(return_value=55)
        monitor = Monitor(self.store, provider, notify)
        await monitor.check_prices()
        await monitor.deliver()
        notify.assert_awaited_once()
        self.assertEqual(provider.fetch.await_count, 3)
        self.assertIn("TimeoutError", self.store.status_text())
        self.assertEqual(self.store.pending(), [])

    async def test_delivery_retry_survives_restart(self):
        self.store.add(Asset.GOLD, Direction.BELOW, Decimal("3000"))
        self.store.record_and_evaluate(quote(), NOW, 900)
        notify = AsyncMock(side_effect=ConnectionError())
        await Monitor(self.store, None, notify).deliver()
        self.assertEqual(len(self.store.pending()), 1)
        self.store.close()
        self.store = Store(self.path)
        notify = AsyncMock(return_value=77)
        monitor = Monitor(self.store, None, notify)
        await monitor.deliver()
        await monitor.deliver()
        notify.assert_awaited_once()
        self.assertEqual(self.store.pending(), [])

    async def test_provider_uses_expected_symbol_and_decimal_parsing(self):
        response = AsyncMock()
        response.raise_for_status = lambda: None
        response.text.return_value = (
            '{"symbol":"BTC","price":12345.123456789,"updatedAt":"2026-09-29T12:00:00Z"}'
        )

        class Session:
            def get(self, url):
                self.url = url
                return response

        response.__aenter__.return_value = response
        session = Session()
        result = await GoldAPI(session).fetch(Asset.BITCOIN)
        self.assertEqual(session.url, "https://api.gold-api.com/price/BTC")
        self.assertEqual(result.price, Decimal("12345.123456789"))

    async def test_cancellation_propagates(self):
        provider = AsyncMock()
        provider.fetch.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await Monitor(self.store, provider, AsyncMock()).check_prices()


class ConfigurationTests(unittest.TestCase):
    def test_defaults_allow_offline_work_but_not_bot_start(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.poll_seconds, 300)
        with self.assertRaises(ValueError):
            settings.require_discord()

    def test_bad_poll_interval_rejected(self):
        for value in ("oops", "-1", "0", "29"):
            with patch.dict("os.environ", {"POLL_SECONDS": value}, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env()

    def test_token_never_shown_in_doctor_or_repr(self):
        env = {
            "DISCORD_TOKEN": "secret-test-token",
            "DISCORD_OWNER_ID": "1",
            "DISCORD_GUILD_ID": "2",
            "DISCORD_CHANNEL_ID": "3",
        }
        output = io.StringIO()
        with patch.dict("os.environ", env, clear=True), redirect_stdout(output):
            result = main(["--env-file", "/nonexistent-env-file", "doctor"])
            self.assertNotIn("secret-test-token", repr(Settings.from_env()))
        self.assertEqual(result, 0)
        self.assertNotIn("secret-test-token", output.getvalue())

    def test_single_instance_lock_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.sqlite3"
            with instance_lock(path):
                with self.assertRaises(ValueError), instance_lock(path):
                    self.fail("Second lock should not be acquired")
            with instance_lock(path):
                pass


if __name__ == "__main__":
    unittest.main()
