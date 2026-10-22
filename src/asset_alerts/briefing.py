"""Market briefing snapshots and text, independent of Discord and chart libraries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from asset_alerts.models import Asset, Direction, Quote, matches

if TYPE_CHECKING:
    from asset_alerts.storage import Store


def _point(quote: Quote) -> dict:
    return {"time": quote.updated_at.astimezone(UTC).isoformat(), "price": str(quote.price)}


def _change(
    history: list[Quote], latest: Quote | None, status: str, cutoff: datetime, tolerance: int
) -> dict:
    result = {"percent": None, "baseline_at": None, "reason": None}
    if status != "fresh":
        result["reason"] = {
            "missing": "no current quote",
            "stale": "latest quote is stale",
            "future": "latest quote is future-dated",
        }[status]
        return result
    # Never use a point after the target time, or a much older point, as its baseline.
    baseline = next((point for point in reversed(history) if point.updated_at <= cutoff), None)
    if baseline is None or (cutoff - baseline.updated_at).total_seconds() > tolerance:
        result["reason"] = "insufficient history near baseline"
        return result
    result["percent"] = str((latest.price - baseline.price) / baseline.price * Decimal(100))
    result["baseline_at"] = baseline.updated_at.astimezone(UTC).isoformat()
    return result


def build_briefing(
    store: "Store", now: datetime, max_age: int, timezone_name: str, poll_seconds: int = 300
) -> dict:
    """Capture prices, comparisons and chart points for durable, repeatable delivery."""
    if now.utcoffset() is None:
        raise ValueError("Briefing timestamps must include a timezone.")
    ZoneInfo(timezone_name)
    now = now.astimezone(UTC)
    window_start = now - timedelta(days=7)
    tolerance = max(max_age, 2 * poll_seconds)
    payload = {
        "version": 1,
        "generated_at": now.isoformat(),
        "timezone": timezone_name,
        "window_start": window_start.isoformat(),
        "gap_seconds": tolerance,
        "assets": [],
    }
    for asset in Asset:
        latest = store.latest(asset)
        if latest is not None and (not latest.price.is_finite() or latest.price <= 0):
            latest = None
        status = "missing"
        if latest is not None:
            age = (now - latest.updated_at).total_seconds()
            status = "future" if age < 0 else "stale" if age > max_age else "fresh"
        history = sorted(
            (
                point
                for point in store.history(asset, window_start - timedelta(seconds=tolerance), now)
                if point.updated_at <= now and point.price.is_finite() and point.price > 0
            ),
            key=lambda point: point.updated_at,
        )
        first, _last, count = store.history_bounds(asset)
        rules = [
            {"id": int(rule["id"]), "direction": rule["direction"], "threshold": rule["threshold"]}
            for rule in store.active_alerts(asset)
        ]
        nearest = None
        if status == "fresh":
            for rule in rules:
                threshold = Decimal(rule["threshold"])
                met = matches(Direction(rule["direction"]), threshold, latest.price)
                distance = Decimal(0) if met else abs(threshold - latest.price)
                movement = "met" if met else "up" if threshold > latest.price else "down"
                candidate = {
                    **rule,
                    "move_direction": movement,
                    "distance_usd": str(distance),
                    "distance_percent": str(distance / latest.price * Decimal(100)),
                }
                if nearest is None or distance < Decimal(nearest["distance_usd"]):
                    nearest = candidate
        payload["assets"].append(
            {
                "asset": asset.value,
                "name": asset.value.title(),
                "unit": asset.unit,
                "price": str(latest.price) if latest else None,
                "quote_time": latest.updated_at.astimezone(UTC).isoformat() if latest else None,
                "status": status,
                "changes": {
                    label: _change(history, latest, status, now - delta, tolerance)
                    for label, delta in (("24h", timedelta(days=1)), ("7d", timedelta(days=7)))
                },
                "nearest_alert": nearest,
                "active_alerts": rules,
                "history": [_point(point) for point in history if point.updated_at >= window_start],
                "history_start": first.astimezone(UTC).isoformat() if first else None,
                "history_count": count,
            }
        )
    return payload


def _money(value: str) -> str:
    price = Decimal(value)
    return f"${price:,.2f}" if price == 0 or abs(price) >= Decimal("0.01") else f"${price:.4g}"


def briefing_text(payload: dict) -> str:
    """Render one compact Discord message from a stored snapshot (no live lookups)."""
    timezone = ZoneInfo(payload["timezone"])
    generated = datetime.fromisoformat(payload["generated_at"]).astimezone(timezone)
    lines = [f"**Morning market briefing · {generated:%b %-d, %Y · %-I:%M %p %Z}**"]
    for asset in payload["assets"]:
        price = f"{_money(asset['price'])}/{asset['unit']}" if asset["price"] else "No quote yet"
        status = {"fresh": "", "stale": " · STALE", "future": " · FUTURE-DATED", "missing": ""}
        lines.extend(["", f"**{asset['name']}** · {price}{status[asset['status']]}"])
        if asset["quote_time"]:
            quote_time = datetime.fromisoformat(asset["quote_time"]).astimezone(timezone)
            lines.append(f"Quote: {quote_time:%b %-d, %-I:%M %p %Z}")
        changes = []
        for label, change in asset["changes"].items():
            if change["percent"] is not None:
                text = f"{Decimal(change['percent']):+.2f}%"
            elif change["reason"] == "insufficient history near baseline":
                text = "insufficient history"
            else:
                text = "unavailable (no fresh quote)"
            changes.append(f"{label}: {text}")
        lines.append(" · ".join(changes))
        nearest = asset["nearest_alert"]
        if nearest:
            target = (
                f"Nearest: #{nearest['id']} {nearest['direction']} {_money(nearest['threshold'])}"
            )
            if nearest["move_direction"] == "met":
                lines.append(f"{target} · condition currently met")
            else:
                percent = Decimal(nearest["distance_percent"])
                lines.append(
                    f"{target} · {nearest['move_direction']} {_money(nearest['distance_usd'])} "
                    f"({percent:.2f}%) away"
                )
        elif asset["active_alerts"]:
            lines.append("Nearest alert: waiting for a fresh quote.")
        else:
            lines.append("No active alerts.")
    lines.extend(
        ["", "USD spot prices · Changes use locally observed history; gaps are not filled."]
    )
    text = "\n".join(lines)
    # Leave room for a short attachment-permission notice in Discord's 2,000-character limit.
    return text if len(text) <= 1850 else text[:1849] + "…"
