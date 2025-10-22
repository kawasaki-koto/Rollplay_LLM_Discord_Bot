import utils.config_manager as config
from .json_handler import load_json, save_json
from utils.console_display import log_system

# メモリ上に全データを保持する辞書
_data_cache = {}

def load_all_data():
    """起動時に全てのJSONファイルを読み込み、メモリにキャッシュする"""
    global _data_cache
    _data_cache = {
        'emotion': load_json(config.EMOTION_FILE),
        'setting': load_json(config.SETTING_FILE),
        'memory': load_json(config.MEMORY_FILE, default_data=[]),
        'schedule': load_json(config.SCHEDULE_FILE),
        'history': load_json(config.HISTORY_FILE, default_data={}),
        'unread': load_json(config.UNREAD_MESSAGES_FILE, default_data={})
    }
    log_system("全てのデータファイルをメモリにロードしました。")

def save_all_data():
    """終了時にメモリ上の全てのデータをJSONファイルに書き出す"""
    if not _data_cache:
        return
    save_json(_data_cache['emotion'], config.EMOTION_FILE)
    save_json(_data_cache['setting'], config.SETTING_FILE)
    # save_json(_data_cache['schedule'], config.SCHEDULE_FILE)
    save_json(_data_cache['history'], config.HISTORY_FILE)
    save_json(_data_cache['unread'], config.UNREAD_MESSAGES_FILE)
    save_json(_data_cache['memory'], config.MEMORY_FILE)
    log_system("全てのデータをファイルに保存しました。")

def get_data(key: str):
    """メモリ上のデータキャッシュへの参照を取得する"""
    return _data_cache.get(key)

def reload_data(key: str):
    """指定されたキーのデータのみをファイルから再読み込みする"""
    if key == 'history':
        _data_cache['history'] = load_json(config.HISTORY_FILE, default_data={})
        from . import ai_request_handler # 循環参照を避けるためここでインポート
        ai_request_handler.initialize_histories() # ai_request_handler側のキャッシュも更新
        log_system("履歴ファイルを再読み込みしました。")
        return True
    
    if key == 'emotion':
        _data_cache['emotion'] = load_json(config.EMOTION_FILE)
        log_system("感情ファイルを再読み込みしました。")
        return True
    
    if key == 'unread':
        _data_cache['unread'] = load_json(config.UNREAD_MESSAGES_FILE, default_data={})
        log_system("未読メッセージファイルを再読み込みしました。")
        return True

    return False
