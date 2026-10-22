import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from asset_alerts.briefing import briefing_text, build_briefing
from asset_alerts.charts import _segments, render_briefing_chart
from asset_alerts.models import Asset, Direction, Quote
from asset_alerts.storage import Store

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "alerts.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, price, when=NOW, asset=Asset.GOLD, collected_at=None):
        self.store.record_and_evaluate(
            Quote(asset, Decimal(price), when), collected_at or when, 900
        )

    def snapshot(self):
        return build_briefing(self.store, NOW, 900, "America/New_York")

    def test_decimal_changes_use_nearby_observation_at_or_before_each_cutoff(self):
        self.record("80", NOW - timedelta(days=7, minutes=10))
        self.record("100.0000000001", NOW - timedelta(days=1, minutes=5))
        self.record("500", NOW - timedelta(days=1) + timedelta(minutes=1))
        self.record("110.0000000001")
        gold = self.snapshot()["assets"][0]
        self.assertEqual(
            Decimal(gold["changes"]["24h"]["percent"]),
            Decimal("10") / Decimal("100.0000000001") * Decimal(100),
        )
        self.assertEqual(
            Decimal(gold["changes"]["7d"]["percent"]),
            (Decimal("110.0000000001") - Decimal(80)) / Decimal(80) * Decimal(100),
        )
        self.assertEqual(
            gold["changes"]["24h"]["baseline_at"],
            (NOW - timedelta(days=1, minutes=5)).isoformat(),
        )
        self.assertEqual(len(gold["history"]), 3)

    def test_old_or_after_cutoff_observations_do_not_stand_in_for_a_missing_baseline(self):
        self.record("80", NOW - timedelta(days=1, seconds=901))
        self.record("100", NOW - timedelta(days=1) + timedelta(seconds=1))
        self.record("110")
        gold = self.snapshot()["assets"][0]
        self.assertIsNone(gold["changes"]["24h"]["percent"])
        self.assertIsNone(gold["changes"]["7d"]["percent"])
        self.assertIn("insufficient history", briefing_text(self.snapshot()))

    def test_tolerance_includes_longer_poll_interval(self):
        self.record("100", NOW - timedelta(days=1, seconds=1200))
        self.record("110")
        payload = build_briefing(self.store, NOW, 900, "America/New_York", poll_seconds=600)
        self.assertEqual(Decimal(payload["assets"][0]["changes"]["24h"]["percent"]), 10)

    def test_new_history_and_missing_assets_are_explicit(self):
        self.record("3000")
        payload = self.snapshot()
        gold, silver, bitcoin = payload["assets"]
        self.assertEqual(gold["history_count"], 1)
        self.assertEqual(gold["history_start"], NOW.isoformat())
        self.assertIsNone(gold["changes"]["24h"]["percent"])
        for asset in (silver, bitcoin):
            self.assertEqual(asset["status"], "missing")
            self.assertEqual(asset["history"], [])
            self.assertIsNone(asset["price"])
        text = briefing_text(payload)
        self.assertIn("8:00 AM EDT", text)
        self.assertIn("No quote yet", text)
        self.assertIn("24h: insufficient history", text)

    def test_stale_latest_price_never_produces_current_changes_or_alert_distances(self):
        self.record("100", NOW - timedelta(days=7))
        self.record("100", NOW - timedelta(days=1))
        self.record("110", NOW - timedelta(seconds=901))
        self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("120"))
        gold = self.snapshot()["assets"][0]
        self.assertEqual(gold["status"], "stale")
        self.assertTrue(all(change["percent"] is None for change in gold["changes"].values()))
        self.assertIsNone(gold["nearest_alert"])
        self.assertIn("STALE", briefing_text(self.snapshot()))
        self.assertIn("waiting for a fresh quote", briefing_text(self.snapshot()))

    def test_small_future_clock_tolerance_does_not_become_briefing_evidence(self):
        self.record("100", NOW - timedelta(days=1))
        self.record("110", NOW + timedelta(seconds=30), collected_at=NOW)
        gold = self.snapshot()["assets"][0]
        self.assertEqual(gold["status"], "future")
        self.assertIsNone(gold["changes"]["24h"]["percent"])
        self.assertEqual(len(gold["history"]), 1)
        self.assertIn("FUTURE-DATED", briefing_text(self.snapshot()))

    def test_nearest_active_alert_has_direction_dollars_and_percent(self):
        self.record("100")
        self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("125"))
        nearest_id = self.store.add(Asset.GOLD, Direction.BELOW, Decimal("95"))
        paused = self.store.add(Asset.GOLD, Direction.BELOW, Decimal("99"))
        self.store.change_state(paused, "pause")
        removed = self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("101"))
        self.store.remove(removed)
        nearest = self.snapshot()["assets"][0]["nearest_alert"]
        self.assertEqual(nearest["id"], nearest_id)
        self.assertEqual(nearest["direction"], "below")
        self.assertEqual(nearest["move_direction"], "down")
        self.assertEqual(Decimal(nearest["distance_usd"]), 5)
        self.assertEqual(Decimal(nearest["distance_percent"]), 5)
        self.assertIn("down $5.00 (5.00%) away", briefing_text(self.snapshot()))

    def test_already_matching_new_alert_requires_no_further_price_movement(self):
        self.record("100")
        self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("90"))
        nearest = self.snapshot()["assets"][0]["nearest_alert"]
        self.assertEqual(nearest["move_direction"], "met")
        self.assertEqual(Decimal(nearest["distance_usd"]), 0)
        self.assertIn("condition currently met", briefing_text(self.snapshot()))

    def test_serialized_snapshot_is_stable_after_new_quotes_and_rule_changes(self):
        self.record("100")
        target = self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("120"))
        payload = self.snapshot()
        serialized = json.dumps(payload)
        text = briefing_text(payload)
        self.record("130", NOW + timedelta(minutes=5))
        self.store.remove(target)
        self.assertEqual(json.dumps(payload), serialized)
        self.assertEqual(briefing_text(json.loads(serialized)), text)
        self.assertIn("$100.00/troy oz", text)

    def test_text_stays_within_limit_with_many_alerts_and_large_prices(self):
        for asset in Asset:
            self.record("999999999999.999", asset=asset)
            for index in range(40):
                self.store.add(asset, Direction.BELOW, Decimal(index + 1))
        text = briefing_text(self.snapshot())
        self.assertLessEqual(len(text), 1850)
        for label in ("Gold", "Silver", "Bitcoin"):
            self.assertIn(label, text)

    def test_naive_snapshot_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            build_briefing(self.store, NOW.replace(tzinfo=None), 900, "America/New_York")

    def test_chart_gaps_split_at_actual_observation_gap(self):
        points = [
            {"time": (NOW + timedelta(seconds=offset)).isoformat(), "price": "100"}
            for offset in (0, 300, 1200, 2101, 2401)
        ]
        parts = _segments(points, 900)
        self.assertEqual([len(part) for part in parts], [3, 2])
        self.assertEqual(parts[0][-1], points[2])
        self.assertEqual(parts[1][0], points[3])

    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib is optional offline")
    def test_png_handles_single_point_empty_flat_gapped_history_and_active_targets(self):
        self.record("3000", NOW - timedelta(hours=2))
        self.record("3000", NOW - timedelta(hours=2) + timedelta(minutes=5))
        self.record("3000")
        self.record("30", asset=Asset.SILVER)
        self.store.add(Asset.GOLD, Direction.ABOVE, Decimal("3200"))
        data = render_briefing_chart(self.snapshot())
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(data), 10000)


if __name__ == "__main__":
    unittest.main()
