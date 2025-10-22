import json
from .console_display import log_info, log_error, log_success

def load_json(file_path: str, default_data=None):
    """
    JSONファイルを安全に読み込みます。
    ファイルが存在しない、または空の場合はデフォルト値を返します。
    """
    if default_data is None:
        default_data = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            log_success("JSON", f"'{file_path}' を読み込みました。")
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        log_info("JSON", f"'{file_path}' が見つからないか不正な形式のため、デフォルトデータで初期化します。")
        save_json(default_data, file_path)
        return default_data
    except Exception as e:
        log_error("JSON", f"'{file_path}' の読み込み中に予期せぬエラー: {e}")
        return default_data

def save_json(data, file_path: str):
    """
    指定されたパスにデータをJSON形式で保存します。
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_error("JSON", f"'{file_path}' の保存中にエラー: {e}")