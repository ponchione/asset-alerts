# Asset Alerts

A personal Discord bot for gold, silver, and bitcoin prices. Runs locally on
Linux and sends alerts to one private channel. Only your Discord account
can use its commands, and only in that channel.

The bot collects local price history and can send a daily market briefing with
charts, price changes, and nearby alert targets. It also supports portable SQLite
backups. The [next-step plan](docs/plan.md) records completed work, future chart
commands, and migration instructions.

## First setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd ~/source/asset-alerts
uv sync
cp .env.example .env
chmod 600 .env
```

Complete the [Discord setup](docs/discord-setup.md), then edit `.env` locally with
your bot token, user ID, server ID, and channel ID. Environment variables take
precedence over `.env`. Relative database paths are relative to the working directory.

```bash
uv run asset-alerts doctor   # checks configuration; never prints the token
uv run asset-alerts prices   # exercises the price feed without using Discord
uv run asset-alerts run      # starts monitoring and registers slash commands
```

In `#price-alerts`, run `/test-alert`, `/prices`, and `/status`. Confirm the test
appears on your phone before relying on alerts. No bot credentials are needed for
the local `prices` command or core tests.

## Commands

| Command | Purpose |
| --- | --- |
| `/prices` | Latest valid quotes from the monitor, with timestamps and stale labels |
| `/briefing` | Private preview of prices, changes, nearest alerts, and seven-day charts |
| `/status` | Check times, feed errors, history coverage, briefing schedule, and pending deliveries |
| `/test-alert` | Send a test message mentioning you in the configured channel |
| `/alert add asset:gold direction:below price:3000` | Create a one-time USD threshold |
| `/alert list page:1` | List rules and delivery state, 10 per page |
| `/alert pause id:1` | Pause an active rule |
| `/alert resume id:1` | Resume a paused rule |
| `/alert remove id:1` | Remove a rule and cancel its unsent notification |

`asset` offers gold, silver, and bitcoin; `direction` offers above and below.
Command responses are visible only to you. Alerts are ordinary channel messages
that mention your configured user. A notification already being sent may finish
before a remove command takes effect.

`/prices` displays each asset in its own spaced block, with a bold name and price
and a smaller update line. Stale quotes carry a separate warning.
Discord timestamps display a readable date and time in your timezone, together
with a live relative age such as "2 minutes ago". Terminal commands use your
desktop's local timezone, for example `Sep 29, 2026 at 9:09 AM EDT`.

Local commands: `asset-alerts run`, `prices`, `status`, `doctor`, `briefing`, and
`backup`. Local `status` reads saved state; it does not prove that a bot process
is currently alive. Local `prices` fetches quotes for inspection without changing
rules or saved state. Local `briefing` previews saved data without fetching prices
or sending a Discord message.

## Daily market briefing

Enable the daily post in `.env` and restart the bot:

```dotenv
BRIEFING_ENABLED=true
BRIEFING_TIME=08:00
BRIEFING_TIMEZONE=America/New_York
```

This schedules a briefing every day at **8:00 AM Eastern**, following daylight
saving time. New installations default to disabled. The time must use 24-hour
`HH:MM` format, the timezone must be a valid IANA name, and the enable setting
must be `true` or `false`; `doctor` validates these settings.

The desktop deployment was enabled on October 3, 2026, with its first scheduled
briefing on October 4 at 8:00 AM EDT. History collection is running for all three
assets. A verified database backup was taken before the upgrade.

Each briefing includes gold, silver, and bitcoin prices with quote timestamps,
24-hour and seven-day percentage changes when enough history exists, the nearest
active alert for each asset, and a PNG with three seven-day price panels. Quote
times and chart labels use the configured briefing timezone. The post goes to the
configured channel without mentioning or pinging you. Grant the bot **Attach
Files** there for the chart; it still sends the text if that permission is missing.

The scheduler checks every 30 seconds and refreshes prices when a new briefing
is due. Feed and Discord delays can push delivery beyond the scheduled minute.
First enabling it after today's scheduled time starts with tomorrow's briefing.
Once enabled, restarting after the scheduled time catches up today's missed
briefing; it does not create reports for earlier missed days.

Each due report is saved as an immutable snapshot before sending. Failed sends
retry oldest first, including after a restart, with the original date and data.
Disabling the schedule preserves queued reports and stops their delivery until
reenabled. Delivery has the same **at least once** guarantee as alerts: a crash
after Discord accepts a message but before SQLite records success can cause a
duplicate. The report date and generation time identify the snapshot.

Run `/briefing` for a private preview at any time. It reads stored prices and
history, makes no price-feed request, and does not consume or change the daily
schedule. Preview it locally or save its chart without sending anything:

```bash
uv run asset-alerts briefing
uv run asset-alerts briefing --chart data/briefing-preview.png
```

The chart path must be new; existing files are never overwritten. Standalone
`/chart` commands and charts on triggered alerts remain future work.

## History and backups

History begins with accepted quotes collected by this version; old prices are
not reconstructed. Only valid, fresh, in-order quotes enter history, and repeated
provider timestamps for the same asset count as one observation. `/status` shows
the available range and observation count for each asset.

Briefing changes need a fresh current quote and an observation at or just before
the 24-hour or seven-day comparison time. The allowed baseline gap is the larger
of `MAX_QUOTE_AGE_SECONDS` and twice `POLL_SECONDS`. Missing comparisons are labeled
as insufficient history; stale quotes are labeled and do not produce changes or
alert distances. Charts label partial coverage and break lines across gaps rather
than filling missing prices. Expect a warmup period, including gaps when metals
markets are closed or the desktop is offline.

Create a consistent, verified backup while the bot runs:

```bash
uv run asset-alerts backup --output data/backups/alerts-2026-10-03.sqlite3
```

This uses SQLite's backup API to write one standalone database containing rules,
history, health, alerts, and queued briefings. It verifies integrity and refuses
to overwrite an existing destination. It excludes `.env` and the Discord token.
Backups are manual; automatic scheduling, rotation, and off-machine copies are
not implemented.

To restore, stop every bot instance using the destination database, preserve its
existing database and companion files separately, and place the snapshot at the
configured database path in a clean directory. Start only one bot and check
`/status`, `/alert list`, and `/briefing`; pending deliveries remain pending in
the restored snapshot. See the [migration instructions](docs/plan.md#moving-from-the-laptop-to-the-main-pc)
for a machine-to-machine transfer and custom database paths.

## Alert behavior

- Gold and silver use USD per troy ounce; bitcoin uses USD per BTC.
- Checks default to every five minutes. Brief movements between checks can be missed.
- Thresholds include equality. A below-$3,000 rule triggers at $3,000 or lower.
- If the target is already met when a rule is added or resumed, the next fresh
  qualifying quote triggers it. There is no requirement to observe a crossing first.
- Each rule fires once. Add a new rule to alert again. Recurring and percentage-change
  rules are future work.
- Prices use `Decimal`. Quotes older than 15 minutes, more than one minute into the
  future, or older than the last accepted quote cannot trigger alerts. A stale
  metals quote during a market closure is expected and is reported in `/status`.
- A failure for one asset does not stop checks for the others. Checks retry on the
  next polling cycle. `/prices` uses saved quotes, so slash commands do not hammer
  the provider.
- SQLite commits the triggered rule and its pending notification together.
  Discord failures leave the notification pending for the next cycle, including
  after restarts. The message includes the original quote timestamp.
- Delivery is **at least once**, not exactly once: a crash after Discord accepts a
  message but before SQLite records success can cause a duplicate on retry. Alert
  IDs identify duplicates. The process lock prevents two local bot instances using
  the same database from sending simultaneously.
- Desktop sleep, internet outages, and Discord notification settings affect timeliness.
  The bot cannot report its own complete outage through Discord while disconnected.

Data comes from [Gold API](https://gold-api.com/docs), using `/price/XAU`,
`/price/XAG`, and `/price/BTC`. See its [response schema](https://gold-api.com/llms.txt)
and [terms](https://gold-api.com/terms). Prices are indicative spot/reference quotes;
dealer premiums and exchange-specific execution prices are outside this first version.

## Run with Docker Engine in WSL

Requires Docker Engine running inside WSL and the Docker Compose plugin. The
container runs in the background with automatic restarts and bounded logs.
Dependencies are installed from `uv.lock` during the image build; host Python
and uv are not required for this deployment.

Create `.env` using the Discord setup above if it does not already exist. Stop
any foreground bot or systemd bot service before starting the container. Keep
only one active bot across machines. If moving an existing database, follow the
[migration plan](docs/plan.md#moving-from-the-laptop-to-the-main-pc) first.

From the project directory:

```bash
mkdir -p data
chmod 700 data
chmod 600 .env
docker compose --env-file /dev/null build
docker compose --env-file /dev/null run --rm asset-alerts doctor
docker compose --env-file /dev/null up -d
docker compose --env-file /dev/null logs --tail 50 -f
```

The bot reads `.env` through a read-only mount at runtime. It is excluded from
the image and build context. `--env-file /dev/null` prevents Compose from also
parsing it for variable substitution. The container runs as UID/GID 1000 by
default, matching the initial WSL user. If your account uses different IDs, run
`export ASSET_ALERTS_UID=$(id -u) ASSET_ALERTS_GID=$(id -g)` before Compose commands.

SQLite is always stored at `./data/alerts.sqlite3` on the host. Compose overrides
`DATABASE_PATH` to `/app/data/alerts.sqlite3` so it stays on the persistent mount.
If the previous installation used a custom path, transfer its stopped database
and companion files to `./data/alerts.sqlite3` before starting. Container rebuilds
and `docker compose down` preserve this host directory.

Run `/status` and `/prices` in Discord to check monitoring, and `/test-alert` in
the configured test channel to verify delivery. `doctor` only checks local
configuration. `docker compose ps` shows process state, not feed health.

Stop with `docker compose --env-file /dev/null stop`. After changing `.env`, run
`docker compose --env-file /dev/null up -d --force-recreate`; after changing code,
run `docker compose --env-file /dev/null up -d --build`.

Docker's `unless-stopped` policy restarts an exited bot while the engine runs;
a deliberately stopped container stays stopped, including after a reboot. Enable
the Docker daemon in WSL so it starts whenever the distribution starts:

```bash
sudo systemctl enable --now docker
```

WSL also needs a foreground process to keep it running after terminals close;
systemd services alone do not provide that. The supplied
[Windows startup installer](deploy/install-windows-startup.ps1) registers a task
that runs `sleep infinity` inside WSL. It starts at Windows boot and retries every
minute if the process exits. It keeps WSL running independently of this session;
Docker's restart policy handles the existing bot container.

Open **Windows PowerShell as administrator using the Windows account that owns
the distribution**, then run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu-26.04\home\mitch\source\asset-alerts\deploy\install-windows-startup.ps1"
Get-ScheduledTask -TaskName 'Asset Alerts - WSL' | Get-ScheduledTaskInfo
```

The defaults match the desktop deployment: distribution `Ubuntu-26.04`, Linux
user `mitch`, task `Asset Alerts - WSL`. Override them with `-Distribution`,
`-LinuxUser`, and `-TaskName` when installing elsewhere. The installer starts the
task immediately. It uses the current Windows account with S4U to run without
an interactive login or a stored password. It does not configure automatic
Windows login. An unrelated task with the same name is refused unless
`-ReplaceExistingTask` is explicitly passed. Disable and stop a running task
before reinstalling it.

For intentional WSL shutdown or task maintenance, first run these in Windows
PowerShell; otherwise the minute trigger can restart WSL:

```powershell
Disable-ScheduledTask -TaskName 'Asset Alerts - WSL'
Stop-ScheduledTask -TaskName 'Asset Alerts - WSL'
```

Resume with `Enable-ScheduledTask -TaskName 'Asset Alerts - WSL'` followed by
`Start-ScheduledTask -TaskName 'Asset Alerts - WSL'`. To remove the startup task,
disable and stop it first, then use
`Unregister-ScheduledTask -TaskName 'Asset Alerts - WSL'`.
These task commands control the WSL keepalive process. Stop the bot itself with
the Compose stop command above.

An on-demand task launch verifies the scheduled-task context; actual startup
before login must be checked after a later Windows reboot. The installer does
not reboot the desktop or stop WSL. Keep the desktop powered, awake, and connected
to the internet. See [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/),
[Microsoft's WSL lifecycle notes](https://learn.microsoft.com/en-us/windows/wsl/systemd),
and [Task Scheduler logon types](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-logontype-principaltype-element).

## Run automatically on Linux with systemd

After the foreground bot and `/test-alert` work, stop the foreground process
with Ctrl+C. The supplied user service assumes this checkout is at
`~/source/asset-alerts` and dependencies were installed with `uv sync`.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/asset-alerts.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now asset-alerts
systemctl --user status asset-alerts
journalctl --user -u asset-alerts -f
```

For monitoring after logout and before login following a reboot, enable lingering
for your Linux account (your desktop may request administrator authentication):

```bash
loginctl enable-linger "$USER"
```

Keep the desktop awake. The service restarts after failures and reads `.env` from
the project directory. Restart it after changing configuration:
`systemctl --user restart asset-alerts`. Stop it with
`systemctl --user stop asset-alerts`. Nothing installs or enables the service automatically.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Core tests have no third-party dependencies and also run with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Discord command tests skip when `discord.py` is unavailable. Tests use fake
prices and notifications; they never connect to Discord or the price API.
`uv.lock` records the resolved dependency versions. Use `uv sync --locked` to
install those versions. If downloads are unavailable, core tests can still run.

Layout:

```text
src/asset_alerts/
  bot.py         Discord commands and background scheduling
  briefing.py    Price comparisons and durable briefing snapshots
  charts.py      Local PNG charts from briefing snapshots
  cli.py         Terminal entry point and single-process lock
  config.py      Environment settings
  daily.py       Timezone-aware daily schedule and delivery retries
  formatting.py  Local dates and Discord timestamps
  models.py      Assets, decimals, timestamps, and threshold comparisons
  provider.py    Price API transport and response validation
  storage.py     SQLite history, rules, queues, health, and verified backups
  monitor.py     Price checks and notification retries
tests/           Offline behavior tests
deploy/          Example systemd user service
docs/            Discord setup and the history/charting/migration plan
```

Secrets, the virtual environment, and `data/` are ignored by Git. Keep exported
backups and charts under `data/` or outside the checkout so private runtime data
is not accidentally committed.
