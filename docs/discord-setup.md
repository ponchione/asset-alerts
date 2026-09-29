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
- Bot permissions: **View Channels** and **Send Messages**.

Open the generated install link and select your private server. Administrator
permission is unnecessary. If `#price-alerts` is a private channel, explicitly
grant the bot access in that channel's permissions. Server-wide permission does
not automatically override a private channel's restrictions.

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

Next use `/prices` and `/status`, then create your first rule with `/alert add`.
Price checks begin when the Discord connection is ready; the initial quote fetch
can take a few seconds. Stop the foreground process with Ctrl+C before starting
the desktop service.

Troubleshooting:

- **No commands:** confirm the server ID, installation scopes, and local process;
  restart the Discord client if needed.
- **Cannot access channel:** check the channel ID, bot membership, and private-channel
  permissions. The bot validates that the configured text channel belongs to the
  configured server at startup.
- **Messages appear but no phone alert:** check Discord's notification, mute, and
  mobile settings with `/test-alert`.
- **No prices:** inspect `/status` or run `uv run asset-alerts prices` locally. A
  closed metals market may return old quotes; stale quotes never trigger rules.

References: [application commands](https://docs.discord.com/developers/docs/interactions/slash-commands),
[Gateway interactions](https://github.com/discord/discord-api-docs/blob/main/developers/interactions/overview.mdx),
[channel permissions](https://support.discord.com/hc/en-us/articles/10543994968087-Channel-Permissions-Settings-101),
[notifications](https://support.discord.com/hc/en-us/articles/215253258-Notifications-Settings-101).
