"""Offline Discord command checks; require installed project dependencies."""

import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from asset_alerts.config import Settings
from asset_alerts.storage import Store


@unittest.skipUnless(importlib.util.find_spec("discord"), "discord.py is not installed")
class DiscordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from asset_alerts.bot import PriceBot

        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "alerts.sqlite3")
        self.bot = PriceBot(Settings(owner_id=1, guild_id=2, channel_id=3), self.store)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()
        self.temp.cleanup()

    def interaction(self, owner=1, guild=2, channel=3):
        return SimpleNamespace(
            user=SimpleNamespace(id=owner),
            guild_id=guild,
            channel_id=channel,
            response=AsyncMock(),
            followup=AsyncMock(),
        )

    async def test_only_configured_owner_guild_and_channel_allowed(self):
        self.assertTrue(await self.bot.tree.interaction_check(self.interaction()))
        for args in ({"owner": 99}, {"guild": 99}, {"channel": 99}, {"guild": None}):
            interaction = self.interaction(**args)
            self.assertFalse(await self.bot.tree.interaction_check(interaction))
            interaction.response.send_message.assert_awaited_once()

    async def test_commands_serialize_without_privileged_intents(self):
        import discord

        guild = discord.Object(id=2)
        commands = self.bot.tree.get_commands(guild=guild)
        self.assertEqual(
            {c.name for c in commands}, {"prices", "status", "test-alert", "alert", "briefing"}
        )
        self.assertEqual(self.bot.tree.get_commands(), [])
        for command in commands:
            self.assertEqual(command.to_dict(self.bot.tree)["name"], command.name)
        self.assertFalse(self.bot.intents.message_content)
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.presences)

    async def test_briefing_preview_is_private_and_does_not_queue_daily_delivery(self):
        import discord

        self.bot.briefing_message = AsyncMock(return_value=("Preview", None))
        interaction = self.interaction()
        command = self.bot.tree.get_command("briefing", guild=discord.Object(id=2))
        await command.callback(interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        self.assertEqual(self.store.pending_briefings(), [])

    async def test_missing_attachment_permission_keeps_text_briefing_available(self):
        from asset_alerts.briefing import build_briefing
        from asset_alerts.models import utcnow

        self.bot.channel = SimpleNamespace(
            guild=SimpleNamespace(me=object()),
            permissions_for=lambda member: SimpleNamespace(attach_files=False),
            send=AsyncMock(return_value=SimpleNamespace(id=42)),
        )
        payload = build_briefing(self.store, utcnow(), 900, "America/New_York")
        self.assertEqual(await self.bot.send_briefing(payload), 42)
        args = self.bot.channel.send.await_args
        self.assertIn("Attach Files", args.args[0])
        self.assertNotIn("file", args.kwargs)
        self.assertFalse(args.kwargs["allowed_mentions"].everyone)

    async def test_slow_briefing_upload_does_not_lock_price_monitor_or_commands(self):
        sending, release = asyncio.Event(), asyncio.Event()

        async def deliver():
            sending.set()
            await release.wait()

        self.bot.daily_briefing = SimpleNamespace(prepare=AsyncMock(), deliver=deliver)
        task = asyncio.create_task(self.bot.briefings.coro(self.bot))
        try:
            await asyncio.wait_for(sending.wait(), timeout=1)
            await asyncio.wait_for(self.bot.operation_lock.acquire(), timeout=1)
            self.bot.operation_lock.release()
        finally:
            release.set()
            await task


if __name__ == "__main__":
    unittest.main()
