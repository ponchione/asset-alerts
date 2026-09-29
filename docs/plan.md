# Price history, charts, and moving to the main PC

Agreed plan as of September 29, 2026. This document describes upcoming work;
history, charting, and a backup command are **not implemented yet**.

## Decisions

- Keep the project free to use. No paid APIs, historical-data subscriptions,
  hosting, or charting services.
- Collect history on the laptop now, once history storage is implemented. There
  is no need to wait for the main PC; the database will move with the application.
- Build basic price history and individual asset charts first. Defer spreads,
  ratios, and more advanced analysis.
- Generate charts locally and post them in the existing private Discord channel.
- Run one active bot instance across both machines. The existing process lock
  only protects instances sharing the same local database, not different PCs.

## Current application

The bot supports gold, silver, and bitcoin prices, one-time threshold alerts,
readable Discord output, and terminal diagnostics. It saves rules, the latest
accepted quote for each asset, delivery records, and health information in SQLite.
It does not yet retain a continuous price history.

The default database is `data/alerts.sqlite3` inside the project directory.
`DATABASE_PATH` in `.env` can override it; `uv run asset-alerts doctor` displays
the configured absolute path without printing the token.

The code and dependency lockfile live in Git. The database, `.env`, virtual
environment, and runtime files are excluded from Git and must be transferred or
recreated separately.

## 1. Retain price history

Extend the existing SQLite database with a price-history table, keeping current
rules, delivery state, and settings intact. Use a versioned, additive migration
that is safe to run again on later startups.

For every new accepted quote, store the asset, decimal price, UTC quote timestamp,
collection timestamp, and source. Continue using the existing free price feed
and five-minute polling interval. Deduplicate repeated provider timestamps per
asset so polling an unchanged quote does not manufacture observations.

Keep the existing freshness and out-of-order checks. Do not insert invalid or
stale responses into the accepted history. Record outages through the existing
health reporting. Retain collected observations initially; add retention or
downsampling only when actual database growth warrants it.

History begins when this feature is enabled. No paid backfill is planned, and
we cannot reconstruct prices missed while the laptop was asleep or disconnected.

Completion criteria:

- Existing databases upgrade without losing alerts or delivery records.
- New observations survive restarts and repeated quotes do not create duplicates.
- A failed asset feed does not stop history collection for the other assets.
- `/status` reports the available history range and observation count per asset.

## 2. Add a portable database backup

Add a terminal command using SQLite's backup API to write a consistent snapshot,
including rules, deliveries, health, and collected history. The snapshot should
be one self-contained SQLite file, even when the source is in WAL mode.

Proposed interface, not available yet:

```text
asset-alerts backup --output /path/to/asset-alerts-backup.sqlite3
```

Refuse to overwrite an existing file by default. Verify the snapshot's integrity
and report its location. Do not include `.env` or the Discord token. Initially,
restoring a snapshot will be a documented operation performed with the bot stopped.

Completion criteria:

- A backup can be taken while the collector is running.
- The snapshot opens independently and contains the expected history and rules.
- Backups do not require a new paid service or a separate SQLite CLI installation.
- Restore instructions preserve pending notifications and require only one active bot.

Reference: [SQLite backup API](https://www.sqlite.org/backup.html).

## 3. Add basic charts in Discord

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

Before enabling chart uploads, grant the bot **Attach Files** in the private
channel. Add **Embed Links** only if the implementation uses embeds. Preserve
owner, server, and channel restrictions; chart replies should remain private to
the command user unless public posting is explicitly selected later.

Completion criteria:

- Charts work from locally collected data with no historical API subscription.
- Sparse history, stale data, and gaps are visible and clearly explained.
- Image generation leaves price checks and other commands responsive.
- Charts display correctly in Discord on desktop and mobile.

## Moving from the laptop to the main PC

This move is possible with the current application and will also preserve history
once that feature is added. The same Discord application, token, server, and
channel can be reused.

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
   Start the bot and check `/prices`, `/status`, `/alert list`, and `/test-alert`.
   Once charting exists, check a chart includes the earlier laptop observations.
6. Install and enable the service on the main PC using the README instructions.
   Keep the laptop instance stopped. Retain the original copy as a backup until
   the move is verified.

Once the backup command exists, a final snapshot taken after stopping the laptop
bot can replace the full-directory transfer in step 3. Put that snapshot at the
configured destination database path in a clean directory before starting the bot.

Do not copy a live SQLite database using ordinary file-copy or cloud-sync tools.
Use the planned backup command for live snapshots, or stop all database users
before copying the database and its companion files together.
[SQLite explains the consistency requirements](https://www.sqlite.org/howtocorrupt.html#_backup_or_restore_while_a_transaction_is_active).

## Deferred ideas

Spreads and asset ratios, normalized performance comparisons, scheduled daily
briefings, percentage-change alerts, recurring alerts, and charts attached to
triggered notifications can follow once history and basic charting are useful.
They are not part of the next implementation pass. Paid data and paid backfills
remain outside the project plan.
