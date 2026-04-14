import re
from deep_translator import GoogleTranslator


def detect_language(text):
    """偵測文字是中文還是日文。回傳 'zh' 或 'ja'。"""
    # 日文假名 (Hiragana + Katakana) 是日文獨有的
    japanese_pattern = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
    if japanese_pattern.search(text):
        return "ja"
    return "zh-TW"


def translate(text, source=None, target=None):
    """翻譯文字。自動偵測方向：中文→日文 / 日文→中文。"""
    if source is None:
        source = detect_language(text)
    if target is None:
        target = "ja" if source.startswith("zh") else "zh-TW"

    translated = GoogleTranslator(source=source, target=target).translate(text)
    return {
        "source_lang": source,
        "target_lang": target,
        "original": text,
        "translated": translated,
    }
