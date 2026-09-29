# Asset Alerts

A personal Discord bot for gold, silver, and bitcoin prices. Runs on an always-on
Linux desktop and sends alerts to one private channel. Only your Discord account
can use its commands, and only in that channel.

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
| `/status` | Check times, feed errors, rule counts, and pending deliveries |
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

Local commands: `asset-alerts run`, `prices`, `status`, and `doctor`. Local `status`
reads saved state; it does not prove that a bot process is currently alive. Local
`prices` fetches quotes for inspection without changing rules or saved state.

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

## Run automatically on Linux

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
If dependency downloads are unavailable, core tests can still run; repeat
`uv sync` when online to generate `uv.lock`, then commit that lockfile.

Layout:

```text
src/asset_alerts/
  bot.py         Discord commands and background scheduling
  cli.py         Terminal entry point and single-process lock
  config.py      Environment settings
  models.py      Assets, decimals, timestamps, and threshold comparisons
  provider.py    Price API transport and response validation
  storage.py     SQLite rules, last quotes, health, and delivery queue
  monitor.py     Price checks and notification retries
tests/           Offline behavior tests
deploy/          Example systemd user service
docs/            Discord setup
```

Secrets, the virtual environment, and `data/` are ignored by Git. To back up the
database, stop the service and copy `data/`, then start it again. The repo is
initialized locally on `main`; add your remote when ready.
