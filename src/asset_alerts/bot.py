"""Owner-only, guild-scoped Discord slash commands."""

import asyncio
import logging
from contextlib import suppress
from decimal import Decimal

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

from asset_alerts.config import Settings
from asset_alerts.formatting import discord_timestamp
from asset_alerts.models import Asset, Direction, positive_price, utcnow
from asset_alerts.monitor import Monitor, alert_message
from asset_alerts.provider import GoldAPI
from asset_alerts.storage import Store

log = logging.getLogger(__name__)


class OwnerTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        settings = self.client.settings
        allowed = (
            interaction.user.id == settings.owner_id
            and interaction.guild_id == settings.guild_id
            and interaction.channel_id == settings.channel_id
        )
        if not allowed:
            await interaction.response.send_message(
                "These commands are limited to the configured owner and alert channel.",
                ephemeral=True,
            )
        return allowed

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        log.error("Command failed (%s)", type(original).__name__)
        message = "Command failed. Check the local service logs and /status."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class PriceBot(discord.Client):
    def __init__(self, settings: Settings, store: Store):
        super().__init__(
            intents=discord.Intents(guilds=True),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(id=settings.owner_id)]
            ),
        )
        self.settings = settings
        self.store = store
        self.tree = OwnerTree(self)
        self.operation_lock = asyncio.Lock()
        self.session = None
        self.channel = None
        self.monitor = None
        register_commands(self)

    async def setup_hook(self) -> None:
        self.channel = await self.fetch_channel(self.settings.channel_id)
        if (
            not isinstance(self.channel, discord.TextChannel)
            or self.channel.guild.id != self.settings.guild_id
        ):
            raise ValueError("DISCORD_CHANNEL_ID must identify a text channel in DISCORD_GUILD_ID.")
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.monitor = Monitor(
            self.store, GoldAPI(self.session), self.send_alert, self.settings.max_quote_age_seconds
        )
        await self.tree.sync(guild=discord.Object(id=self.settings.guild_id))
        self.poll.change_interval(seconds=self.settings.poll_seconds)
        self.poll.start()
        log.info("Commands registered; waiting for Discord connection.")

    async def send_alert(self, delivery) -> int:
        message = await self.channel.send(alert_message(delivery, self.settings.owner_id))
        return message.id

    @tasks.loop(seconds=300)
    async def poll(self) -> None:
        try:
            async with self.operation_lock:
                await self.monitor.check_prices()
                await self.monitor.deliver()
        except Exception as exc:
            log.error("Monitor cycle failed (%s); retrying next cycle.", type(exc).__name__)

    @poll.before_loop
    async def before_poll(self) -> None:
        await self.wait_until_ready()

    async def close(self) -> None:
        self.poll.cancel()
        task = self.poll.get_task()
        if task:
            with suppress(asyncio.CancelledError):
                await task
        if self.session:
            await self.session.close()
        await super().close()


def register_commands(bot: PriceBot) -> None:
    guild = discord.Object(id=bot.settings.guild_id)

    @bot.tree.command(name="prices", description="Show the latest checked prices", guild=guild)
    async def prices(interaction: discord.Interaction):
        blocks = []
        now = utcnow()
        for asset in Asset:
            quote = bot.store.latest(asset)
            if quote is None:
                blocks.append(
                    f"**{asset.value.title()}**\n"
                    "No price available yet.\n"
                    "-# Check /status for details."
                )
                continue
            block = (
                f"**{asset.value.title()}**\n"
                f"**${quote.price:,.2f}** USD / {asset.unit}\n"
                f"-# Updated {discord_timestamp(quote.updated_at)}"
            )
            if not quote.is_fresh(now, bot.settings.max_quote_age_seconds):
                block += "\n⚠️ **Stale quote — waiting for a fresh price.**"
            blocks.append(block)
        await interaction.response.send_message("\n\n".join(blocks), ephemeral=True)

    @bot.tree.command(
        name="status", description="Show price checks and delivery health", guild=guild
    )
    async def status(interaction: discord.Interaction):
        running = "running" if bot.poll.is_running() else "STOPPED"
        await interaction.response.send_message(
            f"Monitor: {running}; checks every {bot.settings.poll_seconds}s\n"
            + bot.store.status_text(format_time=discord_timestamp),
            ephemeral=True,
        )

    @bot.tree.command(
        name="test-alert", description="Send a test alert to this channel", guild=guild
    )
    async def test_alert(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await bot.channel.send(
            f"<@{bot.settings.owner_id}> **Asset Alerts test** — Discord delivery is working."
        )
        await interaction.followup.send(
            "Test sent. Check your device notifications.", ephemeral=True
        )

    alerts = app_commands.Group(name="alert", description="Manage one-time price alerts")

    @alerts.command(name="add", description="Notify once when a price reaches your threshold")
    @app_commands.describe(price="USD price, for example 3000.50 (no dollar sign or commas)")
    async def add(interaction: discord.Interaction, asset: Asset, direction: Direction, price: str):
        try:
            threshold = positive_price(price)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        alert_id = bot.store.add(asset, direction, threshold)
        await interaction.response.send_message(
            f"Created alert #{alert_id}: {asset.value} {direction.value} or equal to "
            f"${threshold:,.2f} USD / {asset.unit}.\n"
            "One-time alert. If the next fresh quote already meets the target, it fires then.",
            ephemeral=True,
        )

    @alerts.command(name="list", description="List your alerts, newest first")
    async def list_alerts(interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1):
        rows = bot.store.alerts(limit=10, offset=(page - 1) * 10)
        lines = [
            f"#{r['id']} {r['asset']} {r['direction']} ${Decimal(r['threshold']):,.2f} "
            f"— {r['state']}" + (f" / {r['delivery_status']}" if r["delivery_status"] else "")
            for r in rows
        ]
        await interaction.response.send_message(
            f"Page {page}\n" + ("\n".join(lines) or "No alerts on this page."), ephemeral=True
        )

    async def change(interaction: discord.Interaction, alert_id: int, action: str):
        await interaction.response.defer(ephemeral=True)
        async with bot.operation_lock:
            changed = (
                bot.store.remove(alert_id)
                if action == "remove"
                else bot.store.change_state(alert_id, action)
            )
        await interaction.followup.send(
            f"Alert #{alert_id}: {action} completed."
            if changed
            else "No change: check the alert ID and state with /alert list.",
            ephemeral=True,
        )

    @alerts.command(name="pause", description="Pause an active alert")
    async def pause(interaction: discord.Interaction, id: app_commands.Range[int, 1]):
        await change(interaction, id, "pause")

    @alerts.command(name="resume", description="Resume a paused alert")
    async def resume(interaction: discord.Interaction, id: app_commands.Range[int, 1]):
        await change(interaction, id, "resume")

    @alerts.command(name="remove", description="Remove an alert and cancel any unsent notification")
    async def remove(interaction: discord.Interaction, id: app_commands.Range[int, 1]):
        await change(interaction, id, "remove")

    bot.tree.add_command(alerts, guild=guild)
