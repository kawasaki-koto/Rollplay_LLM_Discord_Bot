import discord
from discord.ext import commands

from utils import data_manager
from utils.console_display import log_success

class VoiceCog(commands.Cog, name="VoiceCog"):
    def __init__(self, bot):
        self.bot = bot
        # data_managerから設定データを取得
        settings_data = data_manager.get_data('setting')
        self.channel_settings = settings_data.get('channel_settings', {})

    def is_voice_mode_enabled(self, channel_id: int) -> bool:
        """指定されたチャンネルで音声モードが有効かを確認します。"""
        return self.channel_settings.get(str(channel_id), {}).get('voice_mode', False)

async def setup(bot):
    await bot.add_cog(VoiceCog(bot))