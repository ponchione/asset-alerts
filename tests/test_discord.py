"""Offline Discord command checks; require installed project dependencies."""

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
            user=SimpleNamespace(id=owner), guild_id=guild, channel_id=channel, response=AsyncMock()
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
        self.assertEqual({c.name for c in commands}, {"prices", "status", "test-alert", "alert"})
        self.assertEqual(self.bot.tree.get_commands(), [])
        for command in commands:
            self.assertEqual(command.to_dict(self.bot.tree)["name"], command.name)
        self.assertFalse(self.bot.intents.message_content)
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.presences)


if __name__ == "__main__":
    unittest.main()
