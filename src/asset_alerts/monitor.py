"""Independent price checks and durable, retryable notification delivery."""

import logging
from datetime import datetime
from decimal import Decimal

from asset_alerts.models import Asset, utcnow

log = logging.getLogger(__name__)


def alert_message(delivery, owner_id: int) -> str:
    asset = Asset(delivery["asset"])
    return (
        f"<@{owner_id}> **{asset.value.title()} reached your target**\n"
        f"Observed: ${Decimal(delivery['price']):,.2f} USD / {asset.unit}\n"
        f"Target: {delivery['direction']} or equal to ${Decimal(delivery['threshold']):,.2f}\n"
        f"Quote time: {delivery['quote_time']}\n"
        f"Alert #{delivery['alert_id']} · one-time · source: gold-api.com\n"
        "This records the quote when triggered; delivery may be delayed during an outage."
    )


class Monitor:
    def __init__(self, store, provider, notify, max_age: int = 900):
        self.store = store
        self.provider = provider
        self.notify = notify
        self.max_age = max_age

    async def check_prices(self) -> None:
        for asset in Asset:
            try:
                quote = await self.provider.fetch(asset)
                self.store.record_and_evaluate(quote, utcnow(), self.max_age)
            except Exception as exc:
                # A single bad feed must not suppress the other assets. Store
                # only the exception class, keeping network credentials out of logs.
                error = f"Price check failed ({type(exc).__name__}); will retry."
                self.store.health(asset, utcnow(), error)
                log.warning("%s: %s", asset.value, error)

    async def deliver(self) -> None:
        for item in self.store.pending():
            try:
                message_id = await self.notify(item)
            except Exception as exc:
                self.store.failed(
                    item["id"], f"Delivery failed ({type(exc).__name__}); will retry."
                )
                log.warning(
                    "Delivery failed for alert %s (%s)", item["alert_id"], type(exc).__name__
                )
                # Avoid repeatedly hitting an unavailable Discord channel.
                break
            else:
                self.store.delivered(item["id"], message_id)


def price_text(asset: Asset, quote, max_age: int, now: datetime) -> str:
    age = "" if quote.is_fresh(now, max_age) else " [STALE: alerts skipped]"
    return (
        f"{asset.value.title()}: ${quote.price:,.2f} USD / {asset.unit}{age}\n"
        f"  Quote time: {quote.updated_at.isoformat()}"
    )
