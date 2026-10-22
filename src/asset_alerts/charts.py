"""Local PNG charts of captured briefing data; plotting stays outside the domain."""

from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
from threading import Lock
from zoneinfo import ZoneInfo

_RENDER_LOCK = Lock()


def _segments(points: list[dict], gap_seconds: int) -> list[list[dict]]:
    """Split observed points so lines cannot bridge a collection outage."""
    segments = []
    for point in points:
        if not segments or (
            datetime.fromisoformat(point["time"])
            - datetime.fromisoformat(segments[-1][-1]["time"])
        ).total_seconds() > gap_seconds:
            segments.append([])
        segments[-1].append(point)
    return segments


def render_briefing_chart(payload: dict) -> bytes:
    """Render without pyplot/global styling; serialize Matplotlib's shared font machinery."""
    with _RENDER_LOCK:
        return _render(payload)


def _render(payload: dict) -> bytes:
    # Keep optional plotting dependencies out of offline domain/storage tests.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.dates import AutoDateLocator, DateFormatter
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    timezone = ZoneInfo(payload["timezone"])
    generated = datetime.fromisoformat(payload["generated_at"])
    requested_start = datetime.fromisoformat(payload["window_start"])
    observed_times = [
        datetime.fromisoformat(point["time"])
        for asset in payload["assets"]
        for point in asset["history"]
    ]
    start = min(observed_times, default=generated)
    # A single observation still needs a useful time axis, without inventing earlier data.
    axis_start = min(start, generated - timedelta(hours=1))
    partial = start > requested_start + timedelta(seconds=payload["gap_seconds"])
    background, panel, foreground, muted = "#101722", "#172231", "#eff4fc", "#b0bfd0"
    figure = Figure(figsize=(10, 9), dpi=130, facecolor=background)
    canvas = FigureCanvasAgg(figure)
    figure.subplots_adjust(left=0.13, right=0.95, top=0.82, bottom=0.145, hspace=0.65)
    local_now = generated.astimezone(timezone)
    figure.text(0.13, 0.95, "YOUR MORNING MARKETS", color=foreground, size=19, weight="bold")
    figure.text(
        0.13, 0.913, f"{local_now:%b %-d, %Y · %-I:%M %p %Z}", color=muted, size=11
    )
    coverage = "Partial 7-day history" if partial else "Past 7 days"
    observed_range = (
        f"{coverage} · Observations: {start.astimezone(timezone):%b %-d, %-I:%M %p}"
        f" to {max(observed_times).astimezone(timezone):%b %-d, %-I:%M %p %Z}"
        if observed_times else "History collection is starting · No observations available"
    )
    figure.text(
        0.13, 0.882, observed_range, color=muted, size=9,
    )
    colors = {"gold": "#f4ca69", "silver": "#c0d2e3", "bitcoin": "#f59b59"}
    for index, asset in enumerate(payload["assets"], start=1):
        axis = figure.add_subplot(3, 1, index, facecolor=panel)
        color = colors[asset["asset"]]
        points = asset["history"]
        for segment in _segments(points, payload["gap_seconds"]):
            axis.plot(
                [datetime.fromisoformat(point["time"]) for point in segment],
                [float(Decimal(point["price"])) for point in segment],
                color=color, linewidth=1.8,
                marker="." if len(segment) < 12 else None, markersize=5,
            )
        thresholds = {}
        for rule in asset["active_alerts"]:
            key = (rule["direction"], rule["threshold"])
            thresholds.setdefault(key, []).append(rule["id"])
        for (direction, threshold), ids in thresholds.items():
            value = float(Decimal(threshold))
            axis.axhline(value, color="#73859b", linewidth=0.8, linestyle="--", alpha=0.65)
            # Label a bounded number of rules so a busy account cannot cover the chart in text.
            if len(thresholds) <= 4:
                axis.text(
                    0.99, value, f"#{ids[0]} {direction} ${Decimal(threshold):,.2f}",
                    transform=axis.get_yaxis_transform(), ha="right", va="bottom",
                    color=muted, size=8, clip_on=True,
                )
        title = asset["name"]
        if asset["price"]:
            title += f" · ${Decimal(asset['price']):,.2f}"
        if asset["status"] != "fresh":
            title += " · " + asset["status"].upper().replace("MISSING", "NO QUOTE")
        if len(thresholds) > 4:
            title += f" · {len(asset['active_alerts'])} active targets (dashed)"
        axis.set_title(title, loc="left", color=foreground, fontsize=12, pad=10)
        if len(points) < 2:
            message = "Collecting history · one observation" if points else "No observations yet"
            axis.text(
                0.5, 0.5, message, transform=axis.transAxes, ha="center", color=muted, fontsize=10
            )
        elif datetime.fromisoformat(points[0]["time"]) > requested_start + timedelta(
            seconds=payload["gap_seconds"]
        ):
            first = datetime.fromisoformat(points[0]["time"]).astimezone(timezone)
            axis.text(
                0.015, 0.08, f"Available since {first:%b %-d, %-I:%M %p %Z}",
                transform=axis.transAxes, color=muted, size=8,
            )
        axis.set_ylabel(f"USD / {asset['unit']}", color=muted, fontsize=9)
        axis.set_xlim(axis_start, generated + timedelta(seconds=1))
        axis.xaxis.set_major_locator(AutoDateLocator(tz=timezone, minticks=3, maxticks=6))
        date_format = "%b %d\n%I:%M %p" if generated - axis_start < timedelta(days=2) else "%b %d"
        axis.xaxis.set_major_formatter(DateFormatter(date_format, tz=timezone))
        axis.yaxis.set_major_locator(MaxNLocator(nbins=4))
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: f"{value:,.2f}"))
        axis.tick_params(colors=muted, labelsize=8)
        axis.grid(alpha=0.12, color=muted)
        axis.margins(y=0.25)
        for spine in axis.spines.values():
            spine.set_color("#324255")
    figure.text(
        0.13, 0.045,
        f"Times: {payload['timezone']} · Locally observed spot prices · "
        "Dashed lines: active alerts\n"
        f"Line breaks mark gaps over {payload['gap_seconds'] // 60} min; no missing prices filled.",
        color=muted, size=8, linespacing=1.8,
    )
    output = BytesIO()
    canvas.print_png(output)
    return output.getvalue()
