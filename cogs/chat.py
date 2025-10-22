import discord
from discord.ext import commands, tasks
from datetime import datetime
import random
import asyncio

import utils.config_manager as config
from utils.console_display import log_info, log_system, log_success, log_error
from utils import data_manager, ai_request_handler, prompt_builder
from utils import voice_synthesizer

async def send_splittable_message(channel: discord.TextChannel, text: str, file: discord.File = None):
    """
    Discordの文字数制限(2000字)を超えた場合、メッセージを分割して送信する。
    """
    if not text:
        return

    if len(text) <= 2000:
        await channel.send(text, file=file)
        return

    log_info("MESSAGE", f"長文メッセージ({len(text)}文字)を分割して送信します。")
    remaining_text = text
    while len(remaining_text) > 2000:
        # 2000文字地点から最も近い、手前の改行を探す
        split_point = remaining_text.rfind('\n', 0, 2000)
        
        # もし2000文字以内に改行がなければ、強制的に2000文字で分割
        if split_point == -1:
            split_point = 2000
            
        # 最初のチャンクを送信
        await channel.send(remaining_text[:split_point])
        # 残りのテキストを更新（分割後の先頭の空白や改行は削除）
        remaining_text = remaining_text[split_point:].lstrip()
        
        await asyncio.sleep(0.5) # 連投を避けるための短い待機

    # 最後の残った部分を送信
    if remaining_text:
        await channel.send(remaining_text, file=file)

class ChatManagerCog(commands.Cog, name="ChatManagerCog"):
    def __init__(self, bot):
        self.bot = bot
        self.processing_channels = set()
        
        self.unread_data = data_manager.get_data('unread')
        schedule_data = data_manager.get_data('schedule')
        # ★ 修正: 平日と休日のスケジュールをそれぞれ保持
        self.weekday_schedule = schedule_data.get("weekday", {})
        self.weekend_schedule = schedule_data.get("weekend", {})
        self.activity_params = schedule_data.get("activity_params", {})
        
        settings_data = data_manager.get_data('setting')
        self.channel_settings = settings_data.get('channel_settings', {})
        
        self.current_action = "待機中"
        self.current_activity_level = 'normal'
        
        log_system("チャット管理モジュールを初期化し、活動サイクルを開始します。")
        self.activity_loop.start()

    def reset_unread_messages(self):
        """メモリ上の全ての未読メッセージをクリアします。"""
        self.unread_data.clear()
        log_success("UNREAD", "メモリ上の全未読メッセージがリセットされました。")

    def pop_unread_message(self, channel_id: int) -> dict | None:
        """指定されたチャンネルの最も古い未読メッセージを1件削除し、その内容を返します。"""
        str_channel_id = str(channel_id)
        if self.unread_data.get(str_channel_id):
            popped_message = self.unread_data[str_channel_id].pop(0) # 先頭(0番目)を削除
            log_info("UNREAD", f"CH[{channel_id}] の未読メッセージを1件popしました。")
            return popped_message
        return None

    @commands.Cog.listener()
    async def on_message(self, message):
        """メッセージを受信したら未読リストに追加する"""
        if message.author == self.bot.user or message.content.startswith(self.bot.command_prefix):
            return
        
        # ★★★ 修正箇所 ★★★
        # このチャンネルでチャットモードがONになっているか確認
        channel_id_str = str(message.channel.id)
        channel_setting = self.channel_settings.get(channel_id_str, {})
        if not channel_setting.get('chat_mode', False):
            # チャットモードがOFFなら、この先の処理をすべて中断
            return
        
        if channel_id_str not in self.unread_data:
            self.unread_data[channel_id_str] = []
            
        self.unread_data[channel_id_str].append({
            'author': message.author.display_name, 'content': message.content,
            'timestamp': prompt_builder.get_current_time_str()
        })
        log_info("UNREAD", f"[{message.channel.name}] に未読メッセージを1件追加。")

    # ... 以降の関数 (activity_loop, process_channel_activityなど) は変更ありません ...
    @tasks.loop(seconds=1.0)
    async def activity_loop(self):
        """一定時間待機し、ランダムなチャンネルのメッセージ処理または自発的発言を行うループ"""
        now = datetime.now()
        current_hour = str(now.hour)
        # 曜日を取得 (0=月曜日, 5=土曜日, 6=日曜日)
        weekday = now.weekday()

        # 土日(5, 6)かどうかでスケジュールを決定
        active_schedule = self.weekend_schedule if weekday >= 5 else self.weekday_schedule
        
        current_schedule = active_schedule.get(current_hour, {"level": "normal", "action": "🕒 不明"})
        self.current_activity_level = current_schedule['level']
        self.current_action = current_schedule['action']
        
        params = self.activity_params.get(self.current_activity_level, {'seconds': 3600, 'sigma': 900})
        wait_duration = max(60.0, random.normalvariate(params['seconds'], params['sigma']))
        
        log_info("ACTIVITY", f"現在の行動: {self.current_action} | 次の活動まで {wait_duration/60:.2f} 分待機します。")
        await asyncio.sleep(wait_duration)
        
        # 未読があるチャンネルか、デフォルトチャンネルを処理対象候補にする
        default_channel_id = config.get_default_channel_id()
        channels_with_unread = [int(ch_id) for ch_id, msgs in self.unread_data.items() if msgs]
        candidate_channel_ids = channels_with_unread or ([default_channel_id] if default_channel_id else [])
        
        if not candidate_channel_ids:
            log_info("ACTIVITY", "処理対象のチャンネルが見つかりませんでした。")
            return
            
        target_channel_id = random.choice(candidate_channel_ids)
        await self.process_channel_activity(target_channel_id)

        log_info("AUTOSAVE", "自動応答後の定期データ保存を実行します。")
        data_manager.save_all_data()

    async def process_channel_activity(self, channel_id: int):
        """チャンネルの活動（未読処理 or 自発発言）を行う共通関数"""
        str_channel_id = str(channel_id)
        if str_channel_id in self.processing_channels:
            log_info("PROCESS", f"CH[{channel_id}] は処理中のためスキップ。")
            return
        
        target_channel = self.bot.get_channel(channel_id)
        if not target_channel: return

        self.processing_channels.add(str_channel_id)
        try:
            messages_to_process = self.unread_data.get(str_channel_id, [])
            
            # プロンプトを組み立て
            bot_status = prompt_builder.get_bot_status_text(self.bot)
            prompt_instruction = prompt_builder.build_response_prompt(messages_to_process, bot_status)

            # AIに応答を要求
            async with target_channel.typing():
                response_text = await ai_request_handler.send_request(config.MODEL_PRO, prompt_instruction, channel_id=channel_id)

            if not response_text:
                log_error("PROCESS", f"CH[{target_channel.name}] AIからの応答がありませんでした。")
                return
            
            if response_text:
                # ★★★ 音声機能の組み込み ★★★
                voice_cog = self.bot.get_cog("VoiceCog")
                audio_file = None
                text_for_emotion = response_text

                if voice_cog and voice_cog.is_voice_mode_enabled(channel_id):
                    log_info("VOICE", f"CH[{target_channel.name}]で音声合成を実行します。")
                    clean_text, audio_data = await voice_synthesizer.synthesize_speech_with_styles(response_text)
                    if audio_data:
                        audio_file = discord.File(audio_data, filename="voice.wav")
                    text_for_emotion = clean_text # 感情分析にはスタイルタグを除いたテキストを使用

                await send_splittable_message(target_channel, response_text, file=audio_file)
                log_success("PROCESS", f"CH[{target_channel.name}] に応答しました。")

            # 感情を更新し、処理済みの未読をクリア
            if self.bot.get_cog('EmotionCog'):
                user_input = "\n".join(f"[{m['author']}]: {m['content']}" for m in messages_to_process) if messages_to_process else ""
                await self.bot.get_cog('EmotionCog').update_emotions(text_for_emotion, user_input)
            
            if messages_to_process:
                self.unread_data[str_channel_id] = []

        finally:
            self.processing_channels.remove(str_channel_id)

    async def force_check_channel(self, channel_id: int):
        """
        ループの待機を無視して、指定されたチャンネルの活動を即座に処理する
        """
        log_system(f"コマンドにより CH[{channel_id}] の強制チェックを実行します。")
        # 既存の処理関数をそのまま呼び出す
        await self.process_channel_activity(channel_id)
            
    @activity_loop.before_loop
    async def before_activity_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(ChatManagerCog(bot))