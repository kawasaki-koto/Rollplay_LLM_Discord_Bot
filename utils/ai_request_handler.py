import os
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
import asyncio
import collections

import utils.config_manager as config
from utils import data_manager
from utils.console_display import log_info, log_success, log_error

# --- グローバル変数 ---
history_cache = None
channel_locks = collections.defaultdict(asyncio.Lock)
_persona_cache = ""

# --- APIキーの初期設定 ---
API_KEYS = [key for key in [os.getenv('GEMINI_API_KEY_1'), os.getenv('GEMINI_API_KEY_2'), os.getenv('GEMINI_API_KEY_3')] if key]
if not API_KEYS:
    log_error("AI_HANDLER", "有効なGEMINI_API_KEYが.envファイルに見つかりません。")

# --- ペルソナ管理 ---
def load_persona():
    """キャラクターシートをファイルから読み込み、キャッシュする"""
    global _persona_cache
    try:
        with open(config.PERSONA_FILE, 'r', encoding='utf-8') as f:
            _persona_cache = f.read()
        log_success("PERSONA", f"'{config.PERSONA_FILE}' を読み込み、キャッシュしました。")
        return True
    except FileNotFoundError:
        log_error("PERSONA", f"'{config.PERSONA_FILE}' が見つかりません。")
        _persona_cache = "あなたはアシスタントです。"
        return False

def get_persona_message():
    return {"role": "user", "parts": [_persona_cache]}

def apply_persona_to_channel(channel_id: int):
    str_channel_id = str(channel_id)
    if str_channel_id in history_cache:
        history_cache[str_channel_id] = [get_persona_message()]
        log_info("PERSONA", f"CH[{channel_id}] の履歴にペルソナを再適用しました。")

# --- 会話履歴管理 ---
def initialize_histories():
    """会話履歴をデータマネージャーから取得し、ペルソナを読み込む"""
    global history_cache
    history_cache = data_manager.get_data('history')
    load_persona()
    
    if 'default' not in history_cache:
        history_cache['default'] = [
            get_persona_message(),
            {"role": "model", "parts": ["はい、承知いたしました。キャラクターとして応答します。"]}
        ]

def reset_histories():
    """メモリ上の会話履歴を初期化する"""
    global history_cache
    history_cache.clear()
    initialize_histories()
    log_success("AI_HANDLER", "メモリ上の全会話履歴がリセットされました。")

def get_history_for_channel(channel_id: int):
    return history_cache.get(str(channel_id), [])

# --- キー管理 ---
def set_active_key_number(key_num: int):
    settings = data_manager.get_data('setting')
    settings.setdefault('config', {})['active_key'] = key_num
    log_info("AI_HANDLER", f"アクティブなAPIキーを {key_num}番 に更新しました。")

def get_active_key_number():
    settings = data_manager.get_data('setting')
    return settings.get('config', {}).get('active_key', 1)

# --- メインリクエスト関数 ---
async def send_request(model_name: str, prompt: str, channel_id: int | None):
    if not API_KEYS: return "ごめんなさい、APIキーが設定されていません。"

    # ★★★ 修正箇所 ★★★
    # channel_id が None の場合は履歴を使わない一時的なリクエストとして扱う
    is_history_request = channel_id is not None
    
    request_content = []
    if is_history_request:
        str_channel_id = str(channel_id)
        # チャンネルの履歴を取得（なければデフォルト）
        current_history = history_cache.get(str_channel_id, list(history_cache.get('default', [])))
        # 最新のプロンプトを履歴に追加
        current_history.append({"role": "user", "parts": [prompt]})
        request_content = current_history
    else:
        # 履歴を使わないリクエスト（感情分析など）
        request_content = [prompt] # プロンプト文字列を直接渡す

    # 試行するモデルのリストを定義
    models_to_try = []
    if model_name == config.MODEL_PRO:
        models_to_try = [config.MODEL_PRO, config.MODEL_PRO_2, config.MODEL_PRO_3]
    else:
        models_to_try = [model_name]

    async with channel_locks[str(channel_id) if is_history_request else 'system']:
        # モデルを順番に試すための外側ループ
        for current_model in models_to_try:
            start_key_num = get_active_key_number()
            start_index = start_key_num - 1
            ordered_indices = list(range(start_index, len(API_KEYS))) + list(range(0, start_index))

            # APIキーを順番に試すための内側ループ
            for key_index in ordered_indices:
                api_key = API_KEYS[key_index]
                current_key_num = key_index + 1
                log_info("AI_HANDLER", f"APIキー {current_key_num}番 (モデル: {current_model}) でリクエスト試行...")
                
                try:
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel(current_model)
                    response = await model.generate_content_async(request_content)
                    
                    if not response.parts:
                        reason = "Unknown"
                        safety_ratings_text = "N/A"
                        if response.candidates:
                            candidate = response.candidates[0]
                            reason = candidate.finish_reason.name
                            if candidate.safety_ratings:
                                ratings = [f"{rating.category.name}: {rating.probability.name}" for rating in candidate.safety_ratings]
                                safety_ratings_text = ", ".join(ratings)
                        
                        log_error("AI_HANDLER", f"APIキー {current_key_num}番で応答がブロックされました。")
                        log_error("AI_HANDLER", f"  > Finish Reason: {reason}")
                        log_error("AI_HANDLER", f"  > Safety Ratings: {safety_ratings_text}")
                        continue # 次のキーを試す

                    response_text = response.text
                    
                    if is_history_request:
                        current_history.append({"role": "model", "parts": [response_text]})
                        history_cache[str_channel_id] = current_history
                    
                    log_success("AI_HANDLER", f"APIキー {current_key_num}番, モデル '{current_model}' で応答を受信しました。")
                    if current_key_num != start_key_num:
                        set_active_key_number(current_key_num)
                    return response_text # ★ 成功したら即座に結果を返す

                except google_exceptions.ResourceExhausted as e:
                    log_error("AI_HANDLER", f"APIキー {current_key_num}番 がレート制限に達しました。: {e}")
                    continue # 次のキーを試す
                except Exception as e:
                    log_error("AI_HANDLER", f"APIリクエスト中に予期せぬエラー: {type(e).__name__}: {e}")
                    # モデル固有のエラーかもしれないので、次のモデルを試すためにループを抜ける
                    break
            
            # 内側のAPIキーループが全て失敗した場合
            log_error("AI_HANDLER", f"モデル '{current_model}' ですべてのAPIキーが利用できませんでした。")
            
        # 外側のモデルループが全て失敗した場合
        log_error("AI_HANDLER", "試行可能な全てのモデルとAPIキーが利用できませんでした。")
        if is_history_request: current_history.pop()
        return "ごめんなさい、現在AIサービスが利用できないようです。"