# Discord setup

Use your existing private server and create a text channel named `price-alerts`.
The bot runs on your desktop and connects out to Discord. No public URL or port
forwarding is required.

## 1. Create the bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create an application named **Asset Alerts**.
3. Open **Bot**, obtain or reset its token, and put it in your local `.env` as
   `DISCORD_TOKEN`. Keep the token out of Git and chat messages.
4. Leave Message Content, Server Members, and Presence privileged intents disabled.
5. Leave the **Interactions Endpoint URL** blank. This bot receives commands over
   Discord's Gateway connection.

## 2. Install it in your server

Use **OAuth2 → URL Generator** (or the equivalent server installation settings):

- Scopes: `bot` and `applications.commands`.
- Bot permissions: **View Channels**, **Send Messages**, and **Attach Files**.

Open the generated install link and select your private server. Administrator
permission is unnecessary. If `#price-alerts` is a private channel, explicitly
grant the bot access in that channel's permissions. Server-wide permission does
not automatically override a private channel's restrictions.

**Attach Files** allows the PNG charts in daily briefings and private `/briefing`
previews. Existing installations can grant it in the channel's permissions.
Without it, the bot sends the briefing text with a permission notice.

## 3. Copy the IDs

In Discord, enable **User Settings → Advanced → Developer Mode**. Then copy:

| `.env` setting | Where to find it |
| --- | --- |
| `DISCORD_OWNER_ID` | Right-click your user/profile → Copy User ID |
| `DISCORD_GUILD_ID` | Right-click the server icon → Copy Server ID |
| `DISCORD_CHANNEL_ID` | Right-click `#price-alerts` → Copy Channel ID |

IDs are numbers, not names or invite links. `DISCORD_OWNER_ID` is your account,
not the bot's account.

## 4. Test the connection

```bash
uv run asset-alerts doctor
uv run asset-alerts prices
uv run asset-alerts run
```

The bot registers commands only in the configured server. Run `/test-alert` in
the configured channel. Check both the channel and your phone's notifications.
Enable channel/server notifications and mobile push as needed. Commands from
another user or channel are rejected, even if Discord displays them there.

Next use `/prices`, `/status`, and `/briefing`, then create your first rule with
`/alert add`. The briefing preview is private and uses saved quotes and history;
it does not fetch prices or change the daily schedule. Newly collected history
needs time to support 24-hour and seven-day comparisons and charts.
Price checks begin when the Discord connection is ready; the initial quote fetch
can take a few seconds. Stop the foreground process with Ctrl+C before starting
the desktop service.

## 5. Enable the daily briefing

The schedule is disabled by default. For a daily **8:00 AM Eastern** post, add
these settings to `.env` and restart the running bot:

```dotenv
BRIEFING_ENABLED=true
BRIEFING_TIME=08:00
BRIEFING_TIMEZONE=America/New_York
```

The timezone follows daylight saving time. Run `uv run asset-alerts doctor` to
validate the settings. For Docker, restart with
`docker compose --env-file /dev/null up -d --force-recreate`.

The post appears in the configured channel without a mention or ping. It includes
prices, changes when sufficient history exists, nearby alert targets, and a chart.
The scheduler checks every 30 seconds and refreshes prices before creating a due
report; network delays can delay delivery. First enabling after 8:00 AM starts
the following morning. Failed sends remain queued across restarts. `/status`
shows the schedule and pending briefings, and `/briefing` previews the content
without waiting for the next scheduled post.

## Troubleshooting

- **No commands:** confirm the server ID, installation scopes, and local process;
  restart the Discord client if needed.
- **Cannot access channel:** check the channel ID, bot membership, and private-channel
  permissions. The bot validates that the configured text channel belongs to the
  configured server at startup.
- **Messages appear but no phone alert:** check Discord's notification, mute, and
  mobile settings with `/test-alert`.
- **No prices:** inspect `/status` or run `uv run asset-alerts prices` locally. A
  closed metals market may return old quotes; stale quotes never trigger rules.
- **Briefing has no chart:** grant **Attach Files** in the configured channel.
- **Briefing says insufficient history:** keep the bot running to accumulate
  valid observations. Missing history and market-closure gaps are not filled.
- **No daily briefing yet:** check `BRIEFING_ENABLED`, the configured timezone,
  and `/status`. A first activation after the scheduled time starts tomorrow;
  a restart can recover today's previously scheduled report.

References: [application commands](https://docs.discord.com/developers/docs/interactions/slash-commands),
[Gateway interactions](https://github.com/discord/discord-api-docs/blob/main/developers/interactions/overview.mdx),
[channel permissions](https://support.discord.com/hc/en-us/articles/10543994968087-Channel-Permissions-Settings-101),
[notifications](https://support.discord.com/hc/en-us/articles/215253258-Notifications-Settings-101).
