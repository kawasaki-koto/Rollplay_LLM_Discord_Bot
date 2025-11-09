import discord
from discord.ext import commands
import os
import asyncio
import argparse # ★ argparseを追加
from utils import config_manager # ★ config_managerをインポート
from utils.console_display import display_startup_banner, log_system, log_info, log_success, log_error
from utils import data_manager
import utils.config_manager as config

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.presences = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# config_managerにBotインスタンスを設定
config.set_bot_instance(bot)

@bot.event
async def on_ready():
    log_success("SYSTEM", f"キャラクター '{config_manager.CHARACTER_NAME}' が {bot.user} としてログインしました")
    log_system("ユーザーからの接続を待機しています...")

async def load_cogs():
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            try:
                await bot.load_extension(f'cogs.{filename[:-3]}')
                log_info("SYSTEM", f"モジュール '{filename}' のロード完了")
            except Exception as e:
                log_error("SYSTEM", f"モジュール '{filename}' のロード中にエラー: {e}")

async def main():
    # ★★★ 起動引数の解析 ★★★
    parser = argparse.ArgumentParser(description="Discord Bot E.A.S.T")
    parser.add_argument("character", help="起動するキャラクターの名前 (例: haruka)")
    args = parser.parse_args()
    
    # ★★★ config_managerの初期化 ★★★
    if not config_manager.init(args.character):
        return

    data_manager.load_all_data()

    DISCORD_TOKEN = os.getenv(config_manager.TOKEN_ENV_VAR)
    if not DISCORD_TOKEN:
        log_error("SYSTEM", f"環境変数 '{config_manager.TOKEN_ENV_VAR}' が設定されていません。")
        return

    display_startup_banner()
    log_system(f"[{config_manager.CHARACTER_NAME}] 初期化シークエンスを開始します...")
    
    from utils import ai_request_handler
    ai_request_handler.initialize_histories()

    await load_cogs()
    log_success("SYSTEM", "全モジュールのロード完了")
    
    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        log_system("シャットダウン処理を実行します...")
        data_manager.save_all_data()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_system("プログラムが割り込みにより終了しました。")