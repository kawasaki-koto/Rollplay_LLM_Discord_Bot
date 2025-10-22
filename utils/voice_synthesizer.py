import aiohttp
import json
import io
import re
import struct

from utils import config_manager as config
from utils.console_display import log_info, log_error, log_success

async def _synthesize_chunk(session, text: str, style_id: int, speed: float):
    try:
        params = {"text": text, "speaker": style_id}
        async with session.post(f"{config.VOICEVOX_URL}/audio_query", params=params) as response:
            if response.status != 200: return None
            audio_query = await response.json()

        audio_query['speedScale'] = speed
        audio_query['volumeScale'] = 3.0
        
        headers = {"Content-Type": "application/json"}
        async with session.post(f"{config.VOICEVOX_URL}/synthesis", params={"speaker": style_id}, data=json.dumps(audio_query), headers=headers) as response:
            if response.status != 200: return None
            return await response.read()
    except Exception as e:
        log_error("VOICE_SYNTH", f"音声チャンクの合成中にエラー: {e}")
        return None

def create_silent_wav_data(duration_ms: int, sample_rate: int = 24000) -> bytes:
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    num_samples = int(sample_rate * duration_ms / 1000)
    subchunk2_size = num_samples * block_align
    chunk_size = 36 + subchunk2_size

    header = struct.pack('<4sI4s4sIHHIIHH4sI',
                         b'RIFF', chunk_size, b'WAVE', b'fmt ', 16, 1,
                         num_channels, sample_rate, byte_rate, block_align,
                         bits_per_sample, b'data', subchunk2_size)
    
    return header + (b'\x00' * subchunk2_size)

async def synthesize_speech_with_styles(raw_text: str) -> tuple[str, io.BytesIO | None]:
    lines_for_speech = [line for line in raw_text.split('\n') if not line.strip().startswith("> SYSTEM:")]
    text_for_synthesis = "\n".join(lines_for_speech)

    # ★★★ 修正箇所 (正規表現の更新) ★★★
    # speed: にも対応
    parts = re.split(r"(code:\w+|speed:[\d.]+)", text_for_synthesis)
    
    final_chunks = []
    current_style_id = config.VOICEVOX_DEFAULT_STYLE_ID
    current_speed = config.VOICEVOX_SPEED_SCALE # ★ 速度の現在値を保持
    clean_text_parts = []
    
    for part in parts:
        if part.startswith("code:"):
            style_key = part.replace("code:", "").lower()
            current_style_id = config.VOICEVOX_STYLE_MAP.get(style_key, config.VOICEVOX_DEFAULT_STYLE_ID)
        
        # ★★★ 追加箇所 ★★★
        elif part.startswith("speed:"):
            try:
                # speed:1.2 のような形式から数値を取得
                current_speed = float(part.replace("speed:", ""))
            except ValueError:
                # 不正な値の場合はデフォルトに戻す
                current_speed = config.VOICEVOX_SPEED_SCALE
        # ★★★ ここまで ★★★

        else:
            stripped_part = part.strip()
            if not stripped_part: continue
            # 現在のスタイルと速度でチャンクを作成
            final_chunks.append((stripped_part, current_style_id, current_speed))
            clean_text_parts.append(part)
    
    clean_text = "".join(clean_text_parts).strip()
    if not final_chunks: return clean_text, None

    audio_segments = []
    try:
        async with aiohttp.ClientSession() as session:
            for text_chunk, style_id, speed in final_chunks:
                # ★ ログにも速度を表示
                # log_info("VOICE_SYNTH", f"テキスト '{text_chunk}' を Style: {style_id}, Speed: {speed} で合成中...")
                wav_data = await _synthesize_chunk(session, text_chunk, style_id, speed)
                if wav_data:
                    audio_segments.append(wav_data)

            # メモリ解放のために、最後に短いダミークエリを投げる
            log_info("VOICE_SYNTH", "VOICEVOXのメモリを解放します...")
            dummy_params = {"text": " ", "speaker": config.VOICEVOX_DEFAULT_STYLE_ID}
            async with session.post(f"{config.VOICEVOX_URL}/audio_query", params=dummy_params):
                log_success("VOICE_SYNTH", "VOICEVOXのメモリ解放クエリを送信しました。")

    except aiohttp.ClientConnectorError:
        log_error("VOICE_SYNTH", "VOICEVOXエンジンに接続できません。")
        return clean_text, None

    if not audio_segments:
        log_error("VOICE_SYNTH", "音声セグメントの生成に失敗しました。")
        return clean_text, None

    silent_chunk = create_silent_wav_data(500)
    final_wav_data = io.BytesIO()
    if not audio_segments:
        return clean_text, None
        
    final_wav_data.write(audio_segments[0])
    
    if len(audio_segments) > 1:
        log_info("VOICE_SYNTH", f"{len(audio_segments)}個の音声セグメントを1秒の間隔を空けて結合します...")
        for segment in audio_segments[1:]:
            final_wav_data.write(segment[44:])
            final_wav_data.write(silent_chunk[44:])

    total_data_size = final_wav_data.getbuffer().nbytes - 44
    final_wav_data.seek(4)
    final_wav_data.write((total_data_size + 36).to_bytes(4, 'little'))
    final_wav_data.seek(40)
    final_wav_data.write(total_data_size.to_bytes(4, 'little'))
    
    final_wav_data.seek(0)
    log_success("VOICE_SYNTH", "音声ファイルの結合に成功しました。")
    return clean_text, final_wav_data