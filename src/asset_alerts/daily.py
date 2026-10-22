"""Timezone-aware daily briefings with durable delivery, independent of Discord."""

import json
import logging
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from asset_alerts.briefing import build_briefing
from asset_alerts.models import utcnow

log = logging.getLogger(__name__)


def scheduled_for(day: date, time_text: str, timezone_name: str) -> datetime:
    """Use the first repeated time; a nonexistent spring time shifts forward."""
    local = datetime.combine(day, time.fromisoformat(time_text), ZoneInfo(timezone_name))
    return local.astimezone(UTC)


class DailyBriefing:
    def __init__(self, store, settings, refresh, send, clock=utcnow):
        self.store = store
        self.settings = settings
        self.refresh = refresh
        self.send = send
        self.clock = clock

    async def tick(self) -> None:
        await self.prepare()
        await self.deliver()

    async def prepare(self) -> None:
        """Refresh and persist the snapshot while the caller holds its operation lock."""
        if not self.settings.briefing_enabled:
            return
        now = self.clock()
        started = self.store.briefing_started_at(now)
        day = now.astimezone(ZoneInfo(self.settings.briefing_timezone)).date()
        scheduled = scheduled_for(day, self.settings.briefing_time, self.settings.briefing_timezone)
        key = day.isoformat()
        if started <= scheduled <= now and not self.store.briefing_exists(key):
            await self.refresh()
            snapshot_time = self.clock()
            payload = build_briefing(
                self.store,
                snapshot_time,
                self.settings.max_quote_age_seconds,
                self.settings.briefing_timezone,
                self.settings.poll_seconds,
            )
            self.store.queue_briefing(key, scheduled, json.dumps(payload), snapshot_time)

    async def deliver(self) -> None:
        """Send captured snapshots without holding up quote collection or commands."""
        if not self.settings.briefing_enabled:
            return
        for item in self.store.pending_briefings():
            try:
                message_id = await self.send(json.loads(item["payload"]))
            except Exception as exc:
                error = f"Briefing delivery failed ({type(exc).__name__}); will retry."
                self.store.briefing_failed(item["local_date"], error)
                log.warning("%s: %s", item["local_date"], error)
                break
            else:
                self.store.briefing_delivered(item["local_date"], message_id, self.clock())
