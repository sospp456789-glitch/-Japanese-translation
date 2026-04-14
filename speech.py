import os
import uuid
import asyncio
import edge_tts
from groq import Groq
from opencc import OpenCC
from config import GROQ_API_KEY, TEMP_AUDIO_DIR

s2t_converter = OpenCC("s2t")  # 簡體→繁體


def speech_to_text(audio_path):
    """使用 Groq Whisper API 將語音轉為文字。回傳 dict 含 text 和 language。"""
    client = Groq(api_key=GROQ_API_KEY)
    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            file=("audio.m4a", audio_file),
            model="whisper-large-v3",
            response_format="verbose_json",
        )
    text = transcription.text
    language = getattr(transcription, "language", "unknown")

    # 只有確定是中文（沒有假名）時才做簡轉繁
    import re
    has_kana = bool(re.search(r"[\u3040-\u309F\u30A0-\u30FF]", text))
    if not has_kana:
        text = s2t_converter.convert(text)

    return {
        "text": text,
        "language": language,
    }


def text_to_speech(text, lang):
    """使用 edge-tts 將文字轉為語音。回傳音檔路徑。"""
    # 選擇對應語言的語音
    voice_map = {
        "ja": "ja-JP-NanamiNeural",      # 日文女聲
        "zh-TW": "zh-TW-HsiaoChenNeural",  # 台灣中文女聲
        "zh": "zh-TW-HsiaoChenNeural",
    }
    voice = voice_map.get(lang, "ja-JP-NanamiNeural")

    output_path = os.path.join(TEMP_AUDIO_DIR, f"{uuid.uuid4().hex}.mp3")

    async def _generate():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)

    asyncio.run(_generate())
    return output_path
