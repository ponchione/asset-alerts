import asyncio
import json
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asset_alerts.daily import DailyBriefing, scheduled_for
from asset_alerts.storage import Store

DUE = datetime(2026, 10, 3, 12, tzinfo=UTC)
REPORT = {"date": "2026-10-03", "assets": [{"asset": "gold", "price": "3000.12"}]}


class ScheduleTests(unittest.TestCase):
    def test_eastern_morning_follows_daylight_saving_changes(self):
        cases = (
            (date(2026, 3, 7), 13),
            (date(2026, 3, 8), 12),
            (date(2026, 10, 31), 12),
            (date(2026, 11, 1), 13),
        )
        for day, hour in cases:
            with self.subTest(day=day):
                self.assertEqual(
                    scheduled_for(day, "08:00", "America/New_York"),
                    datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
                )

    def test_missing_hour_moves_forward_and_repeated_hour_uses_first_occurrence(self):
        self.assertEqual(
            scheduled_for(date(2026, 3, 8), "02:30", "America/New_York"),
            datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
        )
        self.assertEqual(
            scheduled_for(date(2026, 11, 1), "01:30", "America/New_York"),
            datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        )


class DailyBriefingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "alerts.sqlite3"
        self.store = Store(self.path)
        self.refresh = AsyncMock()
        self.send = AsyncMock(return_value=1234)
        self.build_patch = patch("asset_alerts.daily.build_briefing", return_value=REPORT)
        self.build = self.build_patch.start()

    async def asyncTearDown(self):
        self.build_patch.stop()
        self.store.close()
        self.temp.cleanup()

    def scheduler(self, now, *, enabled=True, time_text="08:00"):
        settings = SimpleNamespace(
            briefing_enabled=enabled,
            briefing_time=time_text,
            briefing_timezone="America/New_York",
            max_quote_age_seconds=900,
            poll_seconds=300,
        )
        return DailyBriefing(
            self.store, settings, self.refresh, self.send, clock=lambda: now
        )

    def restart(self):
        self.store.close()
        self.store = Store(self.path)

    def rows(self):
        return self.store.db.execute("SELECT * FROM briefings ORDER BY local_date").fetchall()

    async def test_queues_once_at_due_time_and_keeps_confirmation_across_restart(self):
        await self.scheduler(DUE - timedelta(minutes=1)).tick()
        self.refresh.assert_not_awaited()
        self.send.assert_not_awaited()

        await self.scheduler(DUE).tick()
        self.refresh.assert_awaited_once_with()
        self.send.assert_awaited_once_with(REPORT)
        self.assertEqual(len(self.rows()), 1)
        row = self.rows()[0]
        self.assertEqual(row["local_date"], "2026-10-03")
        self.assertEqual(datetime.fromisoformat(row["scheduled_at"]), DUE)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["message_id"], "1234")

        self.restart()
        await self.scheduler(DUE + timedelta(hours=1)).tick()
        self.send.assert_awaited_once()
        self.refresh.assert_awaited_once()

    async def test_first_enable_after_cutoff_starts_next_day(self):
        await self.scheduler(DUE - timedelta(hours=1), enabled=False).tick()
        await self.scheduler(DUE + timedelta(hours=1)).tick()
        self.send.assert_not_awaited()
        self.refresh.assert_not_awaited()
        self.assertEqual(self.rows(), [])

        self.restart()
        await self.scheduler(DUE + timedelta(days=1)).tick()
        self.send.assert_awaited_once()
        self.assertEqual(self.rows()[0]["local_date"], "2026-10-04")

    async def test_restart_catches_up_only_current_day_and_preserves_activation(self):
        activated = DUE - timedelta(hours=1)
        await self.scheduler(activated).tick()
        self.restart()

        await self.scheduler(DUE + timedelta(days=3, hours=2)).tick()
        self.assertEqual([row["local_date"] for row in self.rows()], ["2026-10-06"])
        self.send.assert_awaited_once()
        self.assertEqual(self.store.briefing_started_at(DUE + timedelta(days=4)), activated)

    async def test_reenable_keeps_existing_activation_and_recovers_todays_due_report(self):
        await self.scheduler(DUE - timedelta(hours=1)).tick()
        await self.scheduler(DUE, enabled=False).tick()
        self.send.assert_not_awaited()
        self.restart()
        await self.scheduler(DUE + timedelta(hours=1)).tick()
        self.send.assert_awaited_once_with(REPORT)
        self.assertEqual(self.rows()[0]["local_date"], "2026-10-03")

    async def test_failed_payload_survives_restart_and_disable_without_refreshing(self):
        await self.scheduler(DUE - timedelta(minutes=1)).tick()
        self.send.side_effect = ConnectionError("secret request details")
        await self.scheduler(DUE).tick()
        row = self.store.pending_briefings()[0]
        self.assertEqual(json.loads(row["payload"]), REPORT)
        self.assertIn("ConnectionError", row["last_error"])
        self.assertNotIn("secret request details", row["last_error"])
        self.assertEqual(row["attempts"], 1)

        self.restart()
        await self.scheduler(DUE + timedelta(minutes=1), enabled=False).tick()
        self.assertEqual(self.store.pending_briefings()[0]["attempts"], 1)
        self.send.assert_awaited_once()

        self.build.return_value = {"date": "changed", "assets": []}
        self.send.side_effect = None
        await self.scheduler(DUE + timedelta(minutes=2)).tick()
        self.assertEqual(self.send.await_args.args, (REPORT,))
        self.refresh.assert_awaited_once()
        self.build.assert_called_once()
        self.assertEqual(self.store.pending_briefings(), [])
        self.assertEqual(self.rows()[0]["attempts"], 2)
        self.assertIsNone(self.rows()[0]["last_error"])

    async def test_pending_reports_send_oldest_first_and_stop_on_failure(self):
        for days_ago in (1, 2):
            due = DUE - timedelta(days=days_ago)
            local_date = due.date().isoformat()
            self.store.queue_briefing(local_date, due, json.dumps({"date": local_date}), due)
        self.send.side_effect = ConnectionError()
        await self.scheduler(DUE + timedelta(hours=1)).tick()
        self.send.assert_awaited_once_with({"date": "2026-10-01"})
        self.assertEqual([row["attempts"] for row in self.store.pending_briefings()], [1, 0])

        self.restart()
        self.send.reset_mock()
        self.send.side_effect = None
        await self.scheduler(DUE + timedelta(hours=2)).tick()
        self.assertEqual(
            [call.args[0]["date"] for call in self.send.await_args_list],
            ["2026-10-01", "2026-10-02"],
        )
        self.refresh.assert_not_awaited()
        self.assertEqual(self.store.pending_briefings(), [])

    async def test_repeated_daylight_saving_hour_does_not_send_twice(self):
        first_occurrence = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
        await self.scheduler(
            first_occurrence - timedelta(minutes=1), time_text="01:30"
        ).tick()
        await self.scheduler(first_occurrence, time_text="01:30").tick()
        self.restart()
        await self.scheduler(
            first_occurrence + timedelta(hours=1), time_text="01:30"
        ).tick()
        self.send.assert_awaited_once()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["local_date"], "2026-11-01")

    async def test_refresh_cancellation_propagates_without_queueing(self):
        await self.scheduler(DUE - timedelta(minutes=1)).tick()
        self.refresh.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.scheduler(DUE).tick()
        self.assertEqual(self.rows(), [])
        self.send.assert_not_awaited()

    async def test_send_cancellation_propagates_and_preserves_pending_report(self):
        await self.scheduler(DUE - timedelta(minutes=1)).tick()
        self.send.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.scheduler(DUE).tick()
        row = self.store.pending_briefings()[0]
        self.assertEqual(row["attempts"], 0)
        self.assertIsNone(row["last_error"])
        self.assertEqual(json.loads(row["payload"]), REPORT)


if __name__ == "__main__":
    unittest.main()
