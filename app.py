import os
import json
import threading
import time
import traceback
from flask import Flask, request, abort, send_file, render_template, jsonify

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
from linebot.v3.webhooks import (
    MessageEvent,
    FollowEvent,
    TextMessageContent,
    AudioMessageContent,
    ImageMessageContent,
)
from linebot.v3.exceptions import InvalidSignatureError

from config import LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, PORT, TEMP_AUDIO_DIR
from translator import translate, detect_language
from speech import speech_to_text, text_to_speech
from ocr import image_to_text
from usage import record, get_today_summary, get_weekly_summary

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

# 系統指令
USAGE_COMMANDS = {"用量", "用量統計", "usage", "📊"}
HELP_COMMANDS = {"/help", "help", "幫助", "功能", "說明"}

PHRASE_MAP = {label: phrase for label, phrase in QUICK_PHRASES}


WELCOME_MESSAGE = """🎌 歡迎你加入胖虎的 AI 翻譯助手（日本專用）

你的隨身中日翻譯工具，旅途中隨時可用！

📖 輸入 /help 查看完整功能說明"""

HELP_MESSAGE = """📖 胖虎的 AI 翻譯助手 — 功能說明

【💬 文字翻譯】
直接打中文或日文，自動偵測語言並翻譯
翻譯結果以大字卡顯示，方便亮給對方看

【🎤 語音翻譯】
長按麥克風錄音，說中文或日文
Bot 自動辨識語音 → 翻譯 → 回傳文字+語音
語音可以直接播給日本人聽

【📷 圖片翻譯】
拍菜單、路標、指示牌傳給 Bot
自動辨識圖片中的文字並翻譯

【⚡ 常用句快速翻譯】
每次翻譯後底部會出現快捷按鈕：
🙏 謝謝 ｜ 💰 多少錢 ｜ 📍 在哪裡
🚻 廁所 ｜ 🍽 推薦餐點 ｜ 🏨 飯店方向
🚃 車站 ｜ 🆘 求助
一按就翻，自動用禮貌日文表達

【📊 用量查詢】
輸入「用量」查看今日與近 7 天使用統計

祝你日本旅途愉快！🗾✈️"""


def build_quick_reply():
    """建立常用句快速按鈕。"""
    items = []
    for label, phrase in QUICK_PHRASES:
        items.append(
            QuickReplyItem(action=MessageAction(label=label, text=label))
        )
    # 加一個用量查詢按鈕
    items.append(
        QuickReplyItem(action=MessageAction(label="📊 用量", text="用量"))
    )
    return QuickReply(items=items)


# ── Flex Message 大字卡 ──────────────────────────────────────
def build_flex_card(original, translated, source_lang, target_lang):
    """建立翻譯結果的 Flex Message 大字卡。"""
    is_to_japanese = target_lang == "ja"
    accent_color = "#06C755" if is_to_japanese else "#E8344E"
    direction = f"{lang_label(source_lang)} → {lang_label(target_lang)}"

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


# ── 即時翻譯網頁 ─────────────────────────────────────────────
@app.route("/live", methods=["GET"])
def live_translate():
    return render_template("realtime.html")


@app.route("/api/translate", methods=["POST"])
def api_translate():
    """即時翻譯 API — 供網頁版使用。"""
    try:
        data = request.get_json()
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"error": "empty text"}), 400

        result = translate(text)
        record("translate")
        return jsonify({
            "original": result["original"],
            "translated": result["translated"],
            "source_lang": result["source_lang"],
            "target_lang": result["target_lang"],
        })
    except Exception as e:
        print(f"API translate error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


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


# ── 靜態音檔服務 ─────────────────────────────────────────────
@app.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    filepath = os.path.join(TEMP_AUDIO_DIR, filename)
    if not os.path.exists(filepath):
        abort(404)
    return send_file(filepath, mimetype="audio/mpeg")


# ── 歡迎新好友 ───────────────────────────────────────────────
@line_handler.add(FollowEvent)
def handle_follow(event):
    msg = TextMessage(text=WELCOME_MESSAGE)
    msg.quick_reply = build_quick_reply()
    reply(event, [msg])


# ── 處理文字訊息 ─────────────────────────────────────────────
@line_handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_text = event.message.text.strip()
    if not user_text:
        return

    # 系統指令：/help
    if user_text.lower() in HELP_COMMANDS:
        msg = TextMessage(text=HELP_MESSAGE)
        msg.quick_reply = build_quick_reply()
        reply(event, [msg])
        return

    # 系統指令：用量查詢
    if user_text in USAGE_COMMANDS:
        summary = get_today_summary() + "\n\n" + get_weekly_summary()
        reply(event, [TextMessage(text=summary)])
        return

    try:
        # 檢查是否為快速按鈕，替換為完整禮貌用語
        actual_text = PHRASE_MAP.get(user_text, user_text)

        # 翻譯
        result = translate(actual_text)
        record("translate")

        # 生成 TTS
        audio_url = None
        try:
            tts_path = text_to_speech(result["translated"], result["target_lang"])
            tts_filename = os.path.basename(tts_path)
            base_url = get_base_url(request)
            audio_url = f"{base_url}/audio/{tts_filename}"
            record("tts")
        except Exception as e:
            print(f"TTS error: {e}")

        # 回覆 Flex 大字卡 + 語音
        messages = [
            build_flex_card(
                result["original"],
                result["translated"],
                result["source_lang"],
                result["target_lang"],
            )
        ]

        if audio_url:
            duration = estimate_duration(result["translated"])
            messages.append(AudioMessage(originalContentUrl=audio_url, duration=duration))

        messages[-1].quick_reply = build_quick_reply()
        reply(event, messages)

    except Exception as e:
        print(f"handle_text error: {traceback.format_exc()}")
        error_msg = TextMessage(text="⚠️ 翻譯時發生錯誤，請再試一次。\n如果持續失敗，可能是服務暫時忙碌中。")
        error_msg.quick_reply = build_quick_reply()
        reply(event, [error_msg])


# ── 處理語音訊息 ─────────────────────────────────────────────
@line_handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio(event):
    audio_path = None
    try:
        # 下載語音檔
        audio_path = download_audio(event.message.id)

        # 語音轉文字
        stt_result = speech_to_text(audio_path)
        recognized_text = stt_result["text"]
        record("stt")

        # 判斷語言
        text_lang = detect_language(recognized_text)
        if text_lang == "ja":
            source, target = "ja", "zh-TW"
        else:
            source, target = "zh-TW", "ja"

        result = translate(recognized_text, source=source, target=target)
        record("translate")

        # 生成完整語音：原文 + 翻譯一起念
        audio_url = None
        try:
            tts_path = text_to_speech(result["translated"], target)
            tts_filename = os.path.basename(tts_path)
            base_url = get_base_url(request)
            audio_url = f"{base_url}/audio/{tts_filename}"
            record("tts")
        except Exception as e:
            print(f"TTS error: {e}")

        # 回覆 Flex 大字卡 + 語音
        messages = [
            build_flex_card(
                result["original"],
                result["translated"],
                source,
                target,
            )
        ]

        if audio_url:
            duration = estimate_duration(result["translated"])
            messages.append(AudioMessage(originalContentUrl=audio_url, duration=duration))

        messages[-1].quick_reply = build_quick_reply()
        reply(event, messages)

    except Exception as e:
        print(f"handle_audio error: {traceback.format_exc()}")
        error_msg = TextMessage(text="⚠️ 語音辨識失敗，可能原因：\n• 語音太短或太模糊\n• 背景噪音太大\n• 服務暫時忙碌\n\n請再試一次，或改用文字輸入。")
        error_msg.quick_reply = build_quick_reply()
        reply(event, [error_msg])

    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)


# ── 處理圖片訊息 (OCR 翻譯) ──────────────────────────────────
@line_handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    image_path = None
    try:
        # 下載圖片
        image_path = download_image(event.message.id)

        # OCR 辨識文字
        ocr_text = image_to_text(image_path)
        record("ocr")

        if not ocr_text or "無法辨識" in ocr_text:
            msg = TextMessage(text="📷 無法辨識圖片中的文字。\n請確認圖片清晰，且包含文字（如菜單、路標、指示牌）。")
            msg.quick_reply = build_quick_reply()
            reply(event, [msg])
            return

        # 翻譯辨識到的文字
        result = translate(ocr_text)
        record("translate")

        # 生成 TTS
        audio_url = None
        try:
            tts_path = text_to_speech(result["translated"], result["target_lang"])
            tts_filename = os.path.basename(tts_path)
            base_url = get_base_url(request)
            audio_url = f"{base_url}/audio/{tts_filename}"
            record("tts")
        except Exception as e:
            print(f"TTS error: {e}")

        # 回覆：OCR 辨識結果 + 翻譯大字卡 + 語音
        ocr_info = TextMessage(text=f"📷 圖片文字辨識：\n{ocr_text}")

        messages = [
            ocr_info,
            build_flex_card(
                result["original"],
                result["translated"],
                result["source_lang"],
                result["target_lang"],
            ),
        ]

        if audio_url:
            duration = estimate_duration(result["translated"])
            messages.append(AudioMessage(originalContentUrl=audio_url, duration=duration))

        messages[-1].quick_reply = build_quick_reply()
        reply(event, messages)

    except Exception as e:
        print(f"handle_image error: {traceback.format_exc()}")
        error_msg = TextMessage(text="⚠️ 圖片辨識失敗，可能原因：\n• 圖片太模糊或太暗\n• 圖片中沒有文字\n• 服務暫時忙碌\n\n請再試一次。")
        error_msg.quick_reply = build_quick_reply()
        reply(event, [error_msg])

    finally:
        if image_path and os.path.exists(image_path):
            os.remove(image_path)


# ── Helper Functions ──────────────────────────────────────────
def reply(event, messages):
    """回覆 LINE 訊息。"""
    try:
        with ApiClient(line_config) as api_client:
            api = MessagingApi(api_client)
            api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=messages,
                )
            )
    except Exception as e:
        print(f"Reply error: {traceback.format_exc()}")


def download_audio(message_id):
    """從 LINE 伺服器下載語音訊息。"""
    with ApiClient(line_config) as api_client:
        blob_api = MessagingApiBlob(api_client)
        content = blob_api.get_message_content(message_id)

    audio_path = os.path.join(TEMP_AUDIO_DIR, f"{message_id}.m4a")
    with open(audio_path, "wb") as f:
        f.write(content)
    return audio_path


def download_image(message_id):
    """從 LINE 伺服器下載圖片訊息。"""
    with ApiClient(line_config) as api_client:
        blob_api = MessagingApiBlob(api_client)
        content = blob_api.get_message_content(message_id)

    image_path = os.path.join(TEMP_AUDIO_DIR, f"{message_id}.jpg")
    with open(image_path, "wb") as f:
        f.write(content)
    return image_path


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
            try:
                os.remove(filepath)
            except Exception:
                pass


def start_cleanup_timer():
    """每 5 分鐘清理一次暫存音檔。"""
    cleanup_old_audio()
    timer = threading.Timer(300, start_cleanup_timer)
    timer.daemon = True
    timer.start()


if __name__ == "__main__":
    start_cleanup_timer()
    app.run(host="0.0.0.0", port=PORT, debug=False)
