# Price history, daily briefings, charts, and migration

Originally agreed September 29, 2026; implementation status updated October 3,
2026. Price history, manual backups, and daily briefings with charts are now
implemented. Standalone chart commands and automatic backup retention remain
upcoming work.

## Decisions

- Keep the project free to use. No paid APIs, historical-data subscriptions,
  hosting, or charting services.
- Collect history on the main PC using the existing free feed. The laptop bot
  was stopped and a fresh desktop database was approved on October 3, 2026.
- The first history feature is a daily market briefing at 8:00 AM Eastern.
  Defer standalone chart commands, spreads, ratios, and more advanced analysis.
- Generate charts locally and post them in the existing private Discord channel.
- Run one active bot instance across both machines. The existing process lock
  only protects instances sharing the same local database, not different PCs.

## Current application

The bot supports gold, silver, and bitcoin prices, one-time threshold alerts,
daily briefings, private `/briefing` previews, and terminal diagnostics. SQLite
stores rules, latest accepted quotes, accepted price history, health, and durable
queues for alerts and briefing snapshots. Briefings include 24-hour and seven-day
changes, nearest active alert targets, and a locally rendered PNG with one
seven-day panel per asset. The desktop uses Docker in WSL with Windows startup
automation; see the [README](../README.md#run-with-docker-engine-in-wsl).

The default database is `data/alerts.sqlite3` inside the project directory.
`DATABASE_PATH` in `.env` can override it; `uv run asset-alerts doctor` displays
the configured absolute path without printing the token.

The code and dependency lockfile live in Git. The database, `.env`, virtual
environment, and runtime files are excluded from Git and must be transferred or
recreated separately.

## 1. Retain price history — implemented

The existing SQLite database upgrades through a versioned, additive migration
that preserves rules and delivery state and is safe on later startups.

Each new accepted quote stores the asset, decimal price, UTC quote timestamp,
collection timestamp, and source. The existing free price feed and default
five-minute polling interval remain in use. Repeated provider timestamps are
deduplicated per asset, so polling an unchanged quote does not manufacture
observations.

Freshness and out-of-order checks apply before history is accepted. Invalid and
stale responses do not enter history, and failures appear in health reporting.
`/status` reports the available range and observation count for each asset.
Collected observations are retained; retention or downsampling can be added
when actual database growth warrants it.

History begins with collection by this version. No backfill is fabricated, and
prices missed while the desktop was offline cannot be reconstructed. A failed
asset feed does not stop collection for the others.

## 2. Portable database backup — implemented

The terminal command uses SQLite's backup API to write a consistent snapshot
while the bot runs, including rules, history, health, alert deliveries, and queued
briefings. The snapshot is one self-contained SQLite file, even when the source
uses WAL mode:

```bash
uv run asset-alerts backup --output /path/to/new-asset-alerts-backup.sqlite3
```

The command refuses existing destinations, verifies snapshot integrity, and
reports its location. It excludes `.env` and the Discord token and needs neither
a paid service nor a separate SQLite CLI. Restore only with the destination bot
stopped, using a clean destination directory and preserving its previous database
and companion files separately. Start only one bot after restoration.

Backups are manual. Automatic schedules, rotation, retention, and off-machine
backup copies are not implemented yet.

Reference: [SQLite backup API](https://www.sqlite.org/backup.html).

## 3. Daily market briefing — implemented

`BRIEFING_ENABLED` defaults to `false`. The requested schedule is enabled with
`BRIEFING_ENABLED=true`, `BRIEFING_TIME=08:00`, and
`BRIEFING_TIMEZONE=America/New_York`, every day including weekends. Time and
timezone settings are validated, and the timezone follows daylight saving time.
The desktop deployment was upgraded and enabled on October 3, 2026, after a
verified database backup. It is collecting history for all three assets; its
first scheduled briefing is October 4 at 8:00 AM EDT.

The scheduler checks every 30 seconds, refreshes prices when due, and queues an
immutable JSON report before sending to the configured channel without a ping.
Network delays can delay delivery. First activation after the scheduled time
starts the next day. Restarts recover today's missed briefing but do not create
unqueued reports for earlier days. Existing queued reports retry oldest first
with their original date and data, including across restarts. Disabling the
schedule pauses delivery and preserves that queue.

Delivery is at least once: a crash after Discord accepts a post but before SQLite
records success can duplicate it. Report dates and generation times distinguish
snapshots. There is one queued snapshot per local date.

Changes require a fresh current price and a baseline at or just before 24 hours
or seven days ago, within the greater of the maximum quote age or two polling
intervals. Missing comparisons say insufficient history. Stale prices are labeled
and have no calculated change or alert distance. Charts show actual observations,
partial coverage during warmup, and gaps between observations. They use explicit
USD/unit labels and the configured briefing timezone.

`/briefing` shows a private preview from saved data without fetching prices or
consuming the daily schedule. The terminal equivalent never sends a message:

```bash
uv run asset-alerts briefing
uv run asset-alerts briefing --chart data/new-briefing-preview.png
```

The optional PNG destination must not already exist. Discord chart rendering
runs outside the event loop. The bot needs **Attach Files** in the configured
channel for images and falls back to text when that permission is missing.
Owner, server, and channel restrictions apply to the preview command.

## 4. Standalone charts in Discord — upcoming

Proposed command, not available yet:

```text
/chart asset:gold period:7d
```

Start with gold, silver, and bitcoin; offer `24h`, `7d`, and `30d` periods. Read
observations from SQLite, render a PNG locally with a free plotting library such
as Matplotlib, and attach it to the command response. Render outside the bot's
event loop so chart generation does not block commands or monitoring. Defer the
Discord response while the image is being generated.

Use a readable dark theme, explicit USD/unit labels, a clearly labeled timezone,
and the actual time range available. Use the desktop's local timezone for static
chart labels; unlike Discord timestamp text, image labels cannot adapt to each
reader's timezone. Display active threshold rules as labeled horizontal lines.

Show gaps for missing observations rather than drawing an uninterrupted line
through an outage. If less history exists than requested, label the available
coverage. If too few points exist for a useful chart, explain that collection is
still warming up. Do not fabricate data or present sampled five-minute prices as
full market OHLC candles.

Briefing uploads already require **Attach Files** in the private channel.
Add **Embed Links** only if a future implementation uses embeds. Preserve
owner, server, and channel restrictions; chart replies should remain private to
the command user unless public posting is explicitly selected later.

Completion criteria:

- Charts work from locally collected data with no historical API subscription.
- Sparse history, stale data, and gaps are visible and clearly explained.
- Image generation leaves price checks and other commands responsive.
- Charts display correctly in Discord on desktop and mobile.

## Moving from the laptop to the main PC

The initial move was completed October 3, 2026: the user confirmed the laptop bot
was stopped and approved a fresh database on the main desktop, `PONCHIONE_MAIN`.
The following instructions remain a reference for future transfers with saved
rules and history. The same Discord application, token, server, and channel can
be reused.

1. Commit and push the current code, then clone or update the repository on the
   main PC to the same version. Run `uv sync --locked`. Do not start its bot yet.
2. Stop the laptop bot. If using the user service, run
   `systemctl --user disable --now asset-alerts` so it stays off after reboot.
   If running in a terminal, stop it with Ctrl+C.
3. Transfer the entire stopped laptop `data/` directory into a clean destination
   in the main PC's checkout. Include any SQLite companion files. If
   `DATABASE_PATH` was customized, transfer that database and its companion files
   together instead. Preserve any existing destination database separately;
   merging two independently collected databases is outside this plan.
4. Transfer `.env` privately, keeping it outside Git, and restrict its permissions
   with `chmod 600 .env`. Adjust `DATABASE_PATH` if it contains a laptop-specific
   absolute path. Recreate the virtual environment using `uv`; do not copy it.
5. Run `uv run asset-alerts doctor` and `uv run asset-alerts status` on the main PC.
   Start the bot and check `/prices`, `/status`, `/alert list`, and `/test-alert`
   in the configured test channel. Use `/briefing` to inspect retained history.
6. Install and enable Docker/Windows startup automation or the Linux service
   using the README instructions.
   Keep the laptop instance stopped. Retain the original copy as a backup until
   the move is verified.

A final `asset-alerts backup --output ...` snapshot taken after stopping the
source bot can replace the full-directory transfer in step 3. Put that snapshot
at the configured destination database path in a clean directory before starting
the bot. Taking the final transfer snapshot after stopping prevents later source
changes from being lost.

Do not copy a live SQLite database using ordinary file-copy or cloud-sync tools.
Use the backup command for live snapshots, or stop all database users
before copying the database and its companion files together.
[SQLite explains the consistency requirements](https://www.sqlite.org/howtocorrupt.html#_backup_or_restore_while_a_transaction_is_active).

## Deferred ideas

Standalone `/chart` commands, automated backup schedules and retention, spreads
and asset ratios, normalized performance comparisons, percentage-change alerts,
recurring alerts, and charts attached to triggered notifications remain future
work. Paid data and paid backfills remain outside the project plan.
