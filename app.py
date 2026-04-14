import os
import json
import threading
import time
from flask import Flask, request, abort, send_file

from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    AudioMessage,
    FlexMessage,
    FlexContainer,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, AudioMessageContent
from linebot.v3.exceptions import InvalidSignatureError

from config import LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, PORT, TEMP_AUDIO_DIR
from translator import translate, detect_language
from speech import speech_to_text, text_to_speech

app = Flask(__name__)

line_handler = WebhookHandler(LINE_CHANNEL_SECRET)
line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


# ── 常用句 (按鈕顯示 → 實際翻譯內容) ────────────────────────
QUICK_PHRASES = [
    ("🙏 謝謝", "非常感謝你"),
    ("💰 多少錢", "請問這個多少錢呢？"),
    ("📍 在哪裡", "請問在哪裡呢？"),
    ("🚻 廁所", "請問廁所在哪裡呢？"),
    ("🍽 推薦餐點", "請問有推薦的餐點嗎？"),
    ("🏨 飯店方向", "請問飯店怎麼走呢？"),
    ("🚃 車站", "請問車站在哪裡呢？"),
    ("🆘 求助", "不好意思，可以請你幫我一下嗎？"),
]

# 建立按鈕文字 → 實際翻譯內容的對應表
PHRASE_MAP = {label: phrase for label, phrase in QUICK_PHRASES}


def build_quick_reply():
    """建立常用句快速按鈕。"""
    items = []
    for label, phrase in QUICK_PHRASES:
        items.append(
            QuickReplyItem(action=MessageAction(label=label, text=label))
        )
    return QuickReply(items=items)


# ── Flex Message 大字卡 ──────────────────────────────────────
def build_flex_card(original, translated, source_lang, target_lang, audio_url=None):
    """建立翻譯結果的 Flex Message 大字卡。"""
    is_to_japanese = target_lang == "ja"
    accent_color = "#06C755" if is_to_japanese else "#E8344E"
    direction = f"{lang_label(source_lang)} → {lang_label(target_lang)}"

    # Body contents
    body_contents = [
        {
            "type": "text",
            "text": direction,
            "size": "xs",
            "color": "#888888",
            "margin": "none",
        },
        {"type": "separator", "margin": "md"},
        {
            "type": "text",
            "text": original,
            "size": "sm",
            "color": "#888888",
            "wrap": True,
            "margin": "md",
        },
        {
            "type": "text",
            "text": translated,
            "size": "xxl",
            "weight": "bold",
            "color": accent_color,
            "wrap": True,
            "margin": "md",
        },
    ]

    flex_dict = {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": body_contents,
            "paddingAll": "16px",
        },
    }

    return FlexMessage(
        alt_text=f"翻譯：{translated}",
        contents=FlexContainer.from_dict(flex_dict),
    )


# ── Health check ──────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return "AI 語音翻譯 Bot 運作中 🎌"


# ── LINE Webhook ──────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        line_handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# ── 靜態音檔服務 (供 LINE AudioMessage 播放) ─────────────────
@app.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    filepath = os.path.join(TEMP_AUDIO_DIR, filename)
    if not os.path.exists(filepath):
        abort(404)
    return send_file(filepath, mimetype="audio/mpeg")


# ── 處理文字訊息 ─────────────────────────────────────────────
@line_handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_text = event.message.text.strip()
    if not user_text:
        return

    # 檢查是否為快速按鈕的文字，替換為完整禮貌用語
    actual_text = PHRASE_MAP.get(user_text, user_text)

    # 翻譯
    result = translate(actual_text)

    # 生成 TTS
    audio_url = None
    try:
        tts_path = text_to_speech(result["translated"], result["target_lang"])
        tts_filename = os.path.basename(tts_path)
        base_url = get_base_url(request)
        audio_url = f"{base_url}/audio/{tts_filename}"
    except Exception as e:
        print(f"TTS error: {e}")

    # 回覆 Flex 大字卡 + 語音
    messages = [
        build_flex_card(
            result["original"],
            result["translated"],
            result["source_lang"],
            result["target_lang"],
            audio_url,
        )
    ]

    if audio_url:
        duration = estimate_duration(result["translated"])
        messages.append(AudioMessage(originalContentUrl=audio_url, duration=duration))

    # Quick Reply 必須放在最後一則訊息上才會顯示
    messages[-1].quick_reply = build_quick_reply()

    reply(event, messages)


# ── 處理語音訊息 ─────────────────────────────────────────────
@line_handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio(event):
    # 下載語音檔
    audio_path = download_audio(event.message.id)

    # 語音轉文字
    stt_result = speech_to_text(audio_path)
    recognized_text = stt_result["text"]

    # 用文字內容判斷語言（比 Whisper 偵測更準，因為日文有假名）
    text_lang = detect_language(recognized_text)
    if text_lang == "ja":
        source, target = "ja", "zh-TW"
    else:
        source, target = "zh-TW", "ja"

    result = translate(recognized_text, source=source, target=target)

    # 生成 TTS
    audio_url = None
    try:
        tts_path = text_to_speech(result["translated"], target)
        tts_filename = os.path.basename(tts_path)
        base_url = get_base_url(request)
        audio_url = f"{base_url}/audio/{tts_filename}"
    except Exception as e:
        print(f"TTS error: {e}")

    # 回覆 Flex 大字卡 + 語音
    messages = [
        build_flex_card(
            result["original"],
            result["translated"],
            source,
            target,
            audio_url,
        )
    ]

    if audio_url:
        duration = estimate_duration(result["translated"])
        messages.append(AudioMessage(originalContentUrl=audio_url, duration=duration))

    # Quick Reply 必須放在最後一則訊息上才會顯示
    messages[-1].quick_reply = build_quick_reply()

    reply(event, messages)

    # 清理下載的語音檔
    if os.path.exists(audio_path):
        os.remove(audio_path)


# ── Helper Functions ──────────────────────────────────────────
def reply(event, messages):
    """回覆 LINE 訊息。"""
    with ApiClient(line_config) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=messages,
            )
        )


def download_audio(message_id):
    """從 LINE 伺服器下載語音訊息。"""
    with ApiClient(line_config) as api_client:
        blob_api = MessagingApiBlob(api_client)
        content = blob_api.get_message_content(message_id)

    audio_path = os.path.join(TEMP_AUDIO_DIR, f"{message_id}.m4a")
    with open(audio_path, "wb") as f:
        f.write(content)
    return audio_path


def get_base_url(req):
    """取得 server 的公開 base URL（確保 HTTPS）。"""
    url = req.url_root.rstrip("/")
    if url.startswith("http://") and req.headers.get("X-Forwarded-Proto") == "https":
        url = "https://" + url[7:]
    return url


def lang_label(lang_code):
    """語言代碼轉顯示名稱。"""
    labels = {
        "ja": "日文",
        "zh-TW": "中文",
        "zh": "中文",
    }
    return labels.get(lang_code, lang_code)


def estimate_duration(text):
    """估算語音長度 (毫秒)，LINE AudioMessage 需要此欄位。"""
    return max(len(text) * 300, 2000)


# ── 定期清理暫存音檔 ─────────────────────────────────────────
def cleanup_old_audio():
    """清理超過 10 分鐘的暫存音檔。"""
    now = time.time()
    for f in os.listdir(TEMP_AUDIO_DIR):
        filepath = os.path.join(TEMP_AUDIO_DIR, f)
        if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > 600:
            os.remove(filepath)


def start_cleanup_timer():
    """每 5 分鐘清理一次暫存音檔。"""
    cleanup_old_audio()
    timer = threading.Timer(300, start_cleanup_timer)
    timer.daemon = True
    timer.start()


if __name__ == "__main__":
    start_cleanup_timer()
    app.run(host="0.0.0.0", port=PORT, debug=False)
