"""Gold API adapter. Quote validation is deliberately separate from transport."""

import json
from datetime import datetime
from decimal import Decimal

from asset_alerts.models import Asset, Quote, positive_price

BASE_URL = "https://api.gold-api.com"


class QuoteError(ValueError):
    pass


def parse_quote(asset: Asset, payload: object) -> Quote:
    try:
        if not isinstance(payload, dict) or payload.get("symbol") != asset.symbol:
            raise ValueError("Unexpected asset symbol.")
        # The /price/{symbol} endpoint is denominated in USD, including older
        # responses that omit currency. Reject an explicit different currency.
        if payload.get("currency", "USD") != "USD":
            raise ValueError("Expected USD.")
        updated_at = datetime.fromisoformat(payload["updatedAt"])
        if updated_at.tzinfo is None:
            raise ValueError("Quote timestamp must include a timezone.")
        return Quote(asset, positive_price(payload["price"]), updated_at)
    except (KeyError, TypeError, ValueError) as exc:
        raise QuoteError(f"Invalid {asset.value} quote.") from exc


class GoldAPI:
    def __init__(self, session):
        self.session = session

    async def fetch(self, asset: Asset) -> Quote:
        async with self.session.get(f"{BASE_URL}/price/{asset.symbol}") as response:
            response.raise_for_status()
            # Parse JSON numbers directly as decimals, preserving the quote.
            payload = json.loads(await response.text(), parse_float=Decimal)
        return parse_quote(asset, payload)
