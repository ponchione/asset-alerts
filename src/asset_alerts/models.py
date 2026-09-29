"""Domain types; no Discord or network dependencies."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class Asset(StrEnum):
    GOLD = "gold"
    SILVER = "silver"
    BITCOIN = "bitcoin"

    @property
    def symbol(self) -> str:
        return {self.GOLD: "XAU", self.SILVER: "XAG", self.BITCOIN: "BTC"}[self]

    @property
    def unit(self) -> str:
        return "BTC" if self == self.BITCOIN else "troy oz"


class Direction(StrEnum):
    ABOVE = "above"
    BELOW = "below"


def utcnow() -> datetime:
    return datetime.now(UTC)


def positive_price(value: object) -> Decimal:
    try:
        price = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Price must be a positive number, for example 3000.50.") from exc
    if not price.is_finite() or price <= 0 or price > Decimal("1000000000000"):
        raise ValueError("Price must be finite, positive, and at most 1 trillion USD.")
    return price


@dataclass(frozen=True)
class Quote:
    asset: Asset
    price: Decimal
    updated_at: datetime

    def is_fresh(self, now: datetime, max_age_seconds: int) -> bool:
        age = (now - self.updated_at).total_seconds()
        return -60 <= age <= max_age_seconds


def matches(direction: Direction, threshold: Decimal, price: Decimal) -> bool:
    # Thresholds are inclusive: hitting the target is enough.
    return price >= threshold if direction == Direction.ABOVE else price <= threshold
