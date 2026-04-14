"""用量監控模組 — 記錄每日 API 呼叫次數。"""

import json
import os
import threading
from datetime import datetime, date

USAGE_FILE = os.path.join(os.path.dirname(__file__), "usage_data.json")
_lock = threading.Lock()


def _load():
    if os.path.exists(USAGE_FILE):
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data):
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record(api_name):
    """記錄一次 API 呼叫。api_name: 'stt', 'translate', 'tts', 'ocr'"""
    today = date.today().isoformat()
    with _lock:
        data = _load()
        if today not in data:
            data[today] = {}
        data[today][api_name] = data[today].get(api_name, 0) + 1
        _save(data)


def get_today_summary():
    """取得今日用量摘要。"""
    today = date.today().isoformat()
    data = _load()
    today_data = data.get(today, {})

    stt = today_data.get("stt", 0)
    translate_count = today_data.get("translate", 0)
    tts = today_data.get("tts", 0)
    ocr = today_data.get("ocr", 0)
    total = stt + translate_count + tts + ocr

    lines = [
        f"📊 今日用量 ({today})",
        f"",
        f"🎤 語音辨識 (STT): {stt} 次",
        f"📝 翻譯: {translate_count} 次",
        f"🔊 語音合成 (TTS): {tts} 次",
        f"📷 圖片翻譯 (OCR): {ocr} 次",
        f"──────────",
        f"合計: {total} 次",
        f"",
        f"⚡ Groq 免費額度: 14,400 次/天",
        f"📈 STT 使用率: {stt/14400*100:.1f}%",
    ]
    return "\n".join(lines)


def get_weekly_summary():
    """取得近 7 天用量摘要。"""
    data = _load()
    from datetime import timedelta

    lines = ["📊 近 7 天用量統計", ""]
    total_all = 0
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        day_data = data.get(d, {})
        day_total = sum(day_data.values())
        total_all += day_total
        bar = "█" * min(day_total, 30) if day_total > 0 else "·"
        lines.append(f"{d[5:]}: {bar} {day_total}")

    lines.append(f"\n7 天合計: {total_all} 次")
    return "\n".join(lines)
