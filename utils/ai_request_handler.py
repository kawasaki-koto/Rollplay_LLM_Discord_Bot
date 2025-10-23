# ai_request_handler.py

import google.api_core.exceptions
import google.generativeai as genai
import utils.config_manager as config
from utils import data_manager # data_manager をインポート
from utils.console_display import log_system, log_error, log_info, log_warning, log_success
from datetime import datetime
import json
import logging
import traceback
import os
import asyncio
import re

# --- グローバル変数 _histories は削除 ---
# _histories = {} # ← 削除

# APIキーの環境変数名のリスト
API_KEY_ENV_VARS = [
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_1",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
]

# 現在使用中のAPIキーのインデックス
current_api_key_index = 0

def initialize_histories():
    """
    履歴キャッシュの初期化（現在は data_manager.load_all_data() で行われるため、
    この関数は実質的に不要になる可能性がありますが、互換性のために残すか、
    起動時の data_manager 読み込み確認用にするか検討できます）
    """
    if data_manager.get_data('history') is None:
        log_warning("AI_REQUEST_HANDLER", "data_managerの履歴キャッシュがまだ初期化されていません。")
        # 必要であればここで data_manager.load_all_data() を呼ぶか、エラーとする
        # data_manager.load_all_data() # ← main.py で呼ばれるはずなので通常は不要
    else:
        log_system("AIリクエストハンドラー: data_managerの履歴キャッシュを確認しました。")

def _load_persona() -> str | None:
    """ペルソナファイルを読み込む"""
    try:
        if not hasattr(config, 'PERSONA_FILE'):
             log_error("CONFIG_ERROR", "config_managerにPERSONA_FILEが定義されていません。")
             return None
        persona_path = config.PERSONA_FILE
        if os.path.exists(persona_path):
            with open(persona_path, 'r', encoding='utf-8') as f:
                log_info("PERSONA_LOAD", f"{persona_path} からペルソナを読み込みます。")
                return f.read()
        else:
            log_error("PERSONA_LOAD", f"ペルソナファイルが見つかりません: {persona_path}")
            return None
    except Exception as e:
        log_error("PERSONA_LOAD", f"ペルソナファイルの読み込み中にエラー: {e}")
        return None

def get_channel_history(channel_id: int) -> list | None:
    """
    指定されたチャンネルIDの履歴を data_manager._data_cache から取得または初期化。
    取得・初期化に成功した場合はリストを、失敗した場合は None を返す。
    """
    history_cache = data_manager.get_data('history') # ★ data_manager のキャッシュを取得
    if history_cache is None:
        log_error("HISTORY", "data_managerの履歴キャッシュ(_data_cache['history'])が見つかりません。")
        return None # エラーを示すために None を返す

    str_channel_id = str(channel_id)

    # チャンネル履歴が存在しない、または空の場合に初期化
    if str_channel_id not in history_cache or not history_cache[str_channel_id]:
        log_action = "初期化" if str_channel_id not in history_cache else "再初期化"
        log_info("HISTORY", f"CH[{channel_id}] の履歴が見つからないか空のため、ペルソナファイルから{log_action}します。")
        persona_content = _load_persona()
        if persona_content:
            initial_history = [{"role": "user", "parts": [persona_content]}]
            # ★ 直接 data_manager のキャッシュを更新
            history_cache[str_channel_id] = initial_history
            log_success("HISTORY", f"CH[{channel_id}] の履歴をペルソナで正常に{log_action}しました。")
        else:
            log_error("HISTORY", f"CH[{channel_id}] の履歴{log_action}に失敗しました。ペルソナが読み込めません。")
            history_cache[str_channel_id] = [] # 空のリストで初期化しておく

    # 更新されたキャッシュからチャンネル履歴を返す
    # ★ history_cache が None でないことは上で確認済み
    return history_cache.get(str_channel_id) # .get() で安全にアクセス

def add_message_to_history(channel_id: int, role: str, message: str):
    """履歴にメッセージを追加 (data_manager._data_cache を直接更新)"""
    # 履歴リストを取得（存在しなければ初期化も試みる）
    history = get_channel_history(channel_id)
    if history is None:
         log_error("HISTORY_ADD", f"CH[{channel_id}] の履歴リスト取得に失敗したため、メッセージを追加できません。")
         return # 履歴リストが取得できなければ追加しない

    # 履歴制限チェック
    try:
        max_history_length = config.get_max_history_length()
        if len(history) >= max_history_length:
            if len(history) >= 3: # ペルソナ + 1ペア以上ある場合
                 # ペルソナ(最初のuserメッセージ)は削除しない
                 del history[1:3] # インデックス1と2 (ペルソナ直後のペア) を削除
                 log_warning("HISTORY", f"CH[{channel_id}] の履歴が長すぎるため、古い会話ペア(ペルソナ直後)を削除しました。")
            elif len(history) == 2 and history[0].get("role") == "user":
                 # ペルソナ + model応答のみの場合、model応答を削除？(仕様による)
                 # ここでは何もしないか、警告を出す程度が良いかも
                 log_warning("HISTORY", f"CH[{channel_id}] 履歴が最大長ですが、ペルソナと応答のみのため削除しませんでした。")

    except AttributeError:
        log_warning("HISTORY", "configにget_max_history_lengthが見つかりません。履歴制限はスキップされます。")
    except Exception as e:
        log_error("HISTORY", f"履歴削除中にエラー: {e}")

    # ★ history は _data_cache['history'][str_channel_id] への参照なので、
    #    ここに append すれば直接キャッシュが更新される
    history.append({"role": role, "parts": [message]})
    log_info("HISTORY", f"CH[{channel_id}] の履歴に {role} のメッセージを追加しました。 (現在の履歴数: {len(history)})")


async def send_request(model_name: str, prompt: str, channel_id: int = None):
    """AIモデルにリクエストを送信し、応答を取得 (APIキー再試行・レート制限対応付き)"""
    global current_api_key_index
    log_info("AI_REQUEST", f"モデル '{model_name}' へのリクエスト処理を開始します...")
    log_info("AI_REQUEST_DEBUG", f"使用モデル名: {model_name}")

    # --- ユーザーメッセージの履歴追加準備 ---
    user_message_content = None
    if channel_id is not None:
        try:
            if config.bot is None:
                log_error("AI_REQUEST_CONFIG", "config.botがNoneです。Cogにアクセスできません。")
            else:
                chat_cog = config.bot.get_cog('ChatManagerCog')
                if chat_cog:
                    str_channel_id = str(channel_id)
                    unread_messages = chat_cog.unread_data.get(str_channel_id, [])
                    if unread_messages:
                         user_messages_for_history = [
                             f"[{m.get('author','Unknown')} @ {m.get('timestamp','')}]: {m.get('content','')}"
                             for m in unread_messages
                         ]
                         user_message_content = "\n".join(user_messages_for_history)
                else:
                    log_warning("AI_REQUEST_COG", "ChatManagerCogが見つかりません。")
        except Exception as e:
            log_error("AI_REQUEST_HISTORY_PREP", f"履歴準備中にエラー: {e}")
            user_message_content = None
    # ------------------------------------

    # --- 履歴取得（ここで初期化も行われる） ---
    # ★ get_channel_history は data_manager._data_cache['history'] 内のリストへの参照を返す
    history_list_ref = get_channel_history(channel_id) if channel_id is not None else []
    if history_list_ref is None and channel_id is not None:
         log_error("AI_REQUEST", f"CH[{channel_id}] の履歴取得/初期化に失敗したため、リクエストを中止します。")
         return None # 履歴がなければリクエストできない
    # ------------------------------------

    # --- APIキーリスト作成 ---
    api_keys_to_try = []
    for env_var in API_KEY_ENV_VARS:
        key = os.getenv(env_var)
        if key:
            api_keys_to_try.append(key)
    log_info("AI_REQUEST_DEBUG", f"読み込んだAPIキーの数: {len(api_keys_to_try)}")
    if not api_keys_to_try:
        log_error("AI_REQUEST_ERROR", "利用可能なGemini APIキーが環境変数に見つかりません。")
        return None
    # ------------------------------------

    # --- 再試行ループ ---
    last_exception = None
    successful_key = None
    response = None
    max_retries_per_key = 1

    start_index = current_api_key_index if 0 <= current_api_key_index < len(api_keys_to_try) else 0
    ordered_keys = api_keys_to_try[start_index:] + api_keys_to_try[:start_index]

    key_index_to_try = 0
    while key_index_to_try < len(ordered_keys):
        api_key = ordered_keys[key_index_to_try]
        current_index_in_original_list = -1
        try:
            current_index_in_original_list = api_keys_to_try.index(api_key)
        except ValueError:
             log_error("AI_REQUEST_INTERNAL", f"キーインデックスの取得に失敗: {api_key[:5]}...")
             key_index_to_try += 1
             continue

        log_info("AI_REQUEST", f"APIキー {current_index_in_original_list + 1}/{len(api_keys_to_try)} (Index: {current_index_in_original_list}) を使用して試行します...")

        retries_with_current_key = 0
        should_wait_before_next_key = False
        wait_duration = 0

        while retries_with_current_key <= max_retries_per_key:
            try:
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(model_name)

                # ★★★ start_chat に渡す履歴リストの参照を使用 ★★★
                if not history_list_ref or history_list_ref[0].get("role") != "user":
                     log_warning("AI_REQUEST_HISTORY_WARN", f"CH[{channel_id}] の履歴が空か、最初の要素が'user'ではありません。API呼び出しに失敗する可能性があります。History: {history_list_ref}")
                     # 空リストで試行
                     chat = model.start_chat(history=[])
                else:
                     chat = model.start_chat(history=history_list_ref) # ★ ここで参照を渡す

                log_info("AI_REQUEST", f"モデル '{model_name}' にリクエストを送信します...")
                try:
                    api_timeout = config.get_api_timeout()
                except AttributeError:
                    log_warning("AI_REQUEST_CONFIG", "configにget_api_timeoutが見つかりません。デフォルトの120秒を使用します。")
                    api_timeout = 120

                response = await asyncio.wait_for(
                    chat.send_message_async(prompt), # 安全性設定なし
                    timeout=api_timeout
                )
                log_info("AI_REQUEST_DEBUG", "chat.send_message_async の呼び出しが完了しました。")

                if not hasattr(response, 'text'):
                     # (応答オブジェクトのチェック処理)
                     feedback = getattr(response, 'prompt_feedback', None)
                     candidates = getattr(response, 'candidates', [])
                     log_error("AI_RESPONSE", "モデルからの応答に text 属性が含まれていません。")
                     if feedback: log_error("AI_RESPONSE_DEBUG", f"Prompt Feedback: {feedback}")
                     if candidates: log_error("AI_RESPONSE_DEBUG", f"Candidates: {candidates}")
                     else: log_error("AI_RESPONSE_DEBUG", f"受信したresponseオブジェクト: {response}")
                     last_exception = Exception(f"Invalid response object received. Feedback: {feedback}, Candidates: {candidates}")
                     retries_with_current_key = max_retries_per_key + 1
                     continue

                # 成功！
                successful_key = api_key
                current_api_key_index = current_index_in_original_list
                log_success("AI_RESPONSE", f"APIキー {current_index_in_original_list + 1} で応答を受信しました。")
                break # 内側ループ脱出

            except google.api_core.exceptions.ResourceExhausted as e:
                # (レート制限エラーの処理)
                log_warning("AI_REQUEST_RATE_LIMIT", f"レート制限エラー発生 (APIキー {current_index_in_original_list + 1}): {e}")
                last_exception = e
                retries_with_current_key += 1
                retry_delay_seconds = 60
                try:
                    match = re.search(r"Please retry in (\d+\.?\d*)s", str(e))
                    if match: retry_delay_seconds = float(match.group(1)) + 1.5
                except Exception as parse_error:
                    log_warning("AI_REQUEST_RATE_LIMIT", f"待機時間の抽出に失敗: {parse_error}。デフォルトの{retry_delay_seconds}秒を使用します。")
                wait_duration = retry_delay_seconds
                should_wait_before_next_key = True
                if retries_with_current_key <= max_retries_per_key:
                    log_info("AI_REQUEST_RATE_LIMIT", f"{retry_delay_seconds:.1f}秒待機してから同じAPIキーで再試行します (試行 {retries_with_current_key}/{max_retries_per_key})...")
                    await asyncio.sleep(retry_delay_seconds)
                    continue
                else:
                    log_warning("AI_REQUEST_RATE_LIMIT", f"APIキー {current_index_in_original_list + 1} での再試行上限に達しました。")
                    break

            except genai.types.StopCandidateException as e:
                 # (安全性ブロックエラーの処理 - 次のキーへ)
                 log_error("AI_REQUEST_SAFETY", f"コンテンツが安全性によりブロックされました (APIキー {current_index_in_original_list + 1}) - 安全設定削除後も発生?: {e}")
                 try:
                     if response and hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                          log_error("AI_REQUEST_SAFETY", f"Prompt Feedback: {response.prompt_feedback}")
                 except Exception as feedback_error:
                      log_error("AI_REQUEST_SAFETY", f"Feedback取得中にエラー: {feedback_error}")
                 last_exception = e
                 retries_with_current_key = max_retries_per_key + 1
                 break

            except asyncio.TimeoutError:
                # (タイムアウトエラーの処理 - 次のキーへ)
                log_error("AI_REQUEST_ERROR", f"APIリクエストがタイムアウトしました (APIキー {current_index_in_original_list + 1})。")
                last_exception = asyncio.TimeoutError("API request timed out.")
                retries_with_current_key = max_retries_per_key + 1
                break
            except Exception as e:
                # (その他のエラーの処理)
                if "history must begin with a user message" in str(e) or "must alternate between" in str(e):
                    log_error("AI_REQUEST_HISTORY_INVALID", f"履歴形式エラー (APIキー {current_index_in_original_list + 1}): {e}")
                    log_error("AI_REQUEST_HISTORY_INVALID", f"問題の履歴 (先頭5件): {history_list_ref[:5]}")
                    last_exception = e
                    successful_key = None
                    key_index_to_try = len(ordered_keys)
                    break
                else:
                    log_error("AI_REQUEST_ERROR", f"予期せぬエラー (APIキー {current_index_in_original_list + 1}): {type(e).__name__} - {e}")
                    log_error("AI_REQUEST_ERROR", traceback.format_exc())
                    last_exception = e
                    retries_with_current_key = max_retries_per_key + 1
                    break
        # --- 内側ループ終了 ---

        if successful_key:
            break

        if should_wait_before_next_key and wait_duration > 0:
            log_info("AI_REQUEST_RATE_LIMIT", f"{wait_duration:.1f}秒待機してから次のAPIキーを試します...")
            await asyncio.sleep(wait_duration)

        key_index_to_try += 1
    # --- 外側ループ終了 ---

    # --- 最終的な失敗処理 ---
    if successful_key is None:
        log_error("AI_REQUEST_FATAL", "すべてのAPIキーと再試行でリクエストに失敗しました。")
        if last_exception:
             log_error("AI_REQUEST_FATAL", f"最後の試行でのエラー: {type(last_exception).__name__} - {last_exception}")
        return None
    # -----------------------

    # --- 成功時の処理 ---
    response_text = response.text

    # --- リクエスト成功後に履歴を追加 ---
    if channel_id is not None:
        log_info("AI_REQUEST_HISTORY_ADD", f"履歴追加処理を開始: channel_id={channel_id}")
        try:
            # ★ add_message_to_history は内部で get_channel_history を呼ぶので、
            #   ここで history_list_ref を渡す必要はない
            if user_message_content:
                log_info("AI_REQUEST_HISTORY_ADD", f"ユーザーメッセージを履歴に追加試行 (内容冒頭): {user_message_content[:100]}...")
                add_message_to_history(channel_id, "user", user_message_content) # ★ 直接キャッシュを更新
                log_info("AI_REQUEST_HISTORY_ADD", "ユーザーメッセージを履歴に追加完了。")
            else:
                log_info("AI_REQUEST_HISTORY_ADD", "ユーザーメッセージ(user_message_content)が空のため、履歴に追加しません。")

            if response_text:
                 log_info("AI_REQUEST_HISTORY_ADD", f"モデル応答を履歴に追加試行 (内容冒頭): {response_text[:100]}...")
                 add_message_to_history(channel_id, "model", response_text) # ★ 直接キャッシュを更新
                 log_info("AI_REQUEST_HISTORY_ADD", "モデル応答を履歴に追加完了。")
            else:
                 log_info("AI_REQUEST_HISTORY_ADD", "モデル応答(response_text)が空のため、履歴に追加しません。")
        except Exception as history_error:
            log_error("AI_REQUEST_HISTORY_ADD", f"履歴追加中に予期せぬエラーが発生しました: {type(history_error).__name__} - {history_error}")
            log_error("AI_REQUEST_HISTORY_ADD", traceback.format_exc())
    else:
        log_warning("AI_REQUEST_HISTORY_ADD", "channel_idがNoneのため、履歴は追加されません。")
    # -----------------------------

    # --- トークン数をログに出力 ---
    try:
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            prompt_token_count = response.usage_metadata.prompt_token_count
            candidates_token_count = response.usage_metadata.candidates_token_count
            total_token_count = response.usage_metadata.total_token_count
            log_info("TOKEN_COUNT", f"Prompt: {prompt_token_count}, Candidates: {candidates_token_count}, Total: {total_token_count}")
        else:
            log_info("TOKEN_COUNT", "Usage metadata not available.")
    except Exception as token_error:
        log_error("AI_REQUEST_TOKEN_LOG", f"トークン数ログ出力中にエラー: {token_error}")
    # -----------------------------

    return response_text