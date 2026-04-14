# CLAUDE.md — AI 語音翻譯 LINE Bot

## Overview

日本旅行用的隨身 AI 語音翻譯工具，整合在 LINE 上，支援中文 ↔ 日文雙向語音與文字翻譯。全部使用免費 API 方案。

## Architecture

```
LINE App (語音/文字訊息)
    ↓ POST /webhook
Flask Server (Python, 部署在 Fly.io)
    ├── STT:  Groq Whisper API (免費, 語音→文字)
    ├── 翻譯: deep-translator (Google Translate, 免費)
    └── TTS:  edge-tts (Microsoft Edge TTS, 免費, 語音合成)
    ↓ reply message
LINE App (文字回覆 + 語音檔)
```

## Tech Stack

| 功能 | 方案 | 免費額度 |
|------|------|----------|
| LINE Bot | Messaging API (reply message) | Reply 訊息無限制 |
| 語音轉文字 (STT) | Groq Whisper API | 14,400 req/day |
| 翻譯 | deep-translator (Google backend) | 無硬性限制 |
| 語音合成 (TTS) | edge-tts | 無限制 |
| 部署 | Fly.io | 3 shared VMs free |

## Project Structure

```
翻譯工具AI/
├── CLAUDE.md           # 本檔案 — 專案說明與開發指引
├── app.py              # Flask + LINE webhook 主程式
├── speech.py           # STT (Groq Whisper) + TTS (edge-tts)
├── translator.py       # 語言偵測 + 翻譯邏輯
├── config.py           # 環境變數管理
├── requirements.txt    # Python 依賴
├── Dockerfile          # 容器化部署
├── Procfile            # Fly.io 啟動指令
├── fly.toml            # Fly.io 配置
├── .env.example        # 環境變數範本
└── .gitignore          # Git 忽略清單
```

## Required API Keys

只需要 2 組免費 API Key：

1. **LINE Channel** — 到 [LINE Developers](https://developers.line.biz/) 建立 Messaging API Channel
   - 取得 `LINE_CHANNEL_SECRET` 和 `LINE_CHANNEL_ACCESS_TOKEN`
2. **Groq API Key** — 到 [groq.com](https://console.groq.com/) 免費註冊
   - 取得 `GROQ_API_KEY`

## Environment Variables

```
LINE_CHANNEL_SECRET=<your-line-channel-secret>
LINE_CHANNEL_ACCESS_TOKEN=<your-line-channel-access-token>
GROQ_API_KEY=<your-groq-api-key>
```

## Commands

```bash
# 本地開發
pip install -r requirements.txt
python app.py                     # 啟動 Flask server (port 8080)

# 本地測試 webhook (需要 ngrok)
ngrok http 8080                   # 產生公開 URL，填入 LINE webhook

# 部署到 Fly.io
fly launch                        # 首次建立
fly deploy                        # 更新部署
fly secrets set LINE_CHANNEL_SECRET=xxx LINE_CHANNEL_ACCESS_TOKEN=xxx GROQ_API_KEY=xxx
```

## User Flow

### 語音翻譯
1. 使用者在 LINE 傳送語音訊息（說中文或日文）
2. Bot 下載音檔 → Groq Whisper 辨識文字並偵測語言
3. 自動翻譯到對應語言（中→日 / 日→中）
4. 回傳：原文 + 譯文 + TTS 語音檔

### 文字翻譯
1. 使用者在 LINE 輸入文字（中文或日文）
2. Bot 偵測語言 → 翻譯
3. 回傳：譯文 + TTS 語音檔（方便直接播放給對方聽）

## Key Design Decisions

- **Reply Message 而非 Push Message**：LINE 免費方案中 reply 不計費，push 每月僅 200 則
- **Groq Whisper 而非本地 Whisper**：伺服器不需 GPU，Groq 免費且回應快（<1秒）
- **edge-tts 而非 Google TTS**：完全免費無限制，日文和中文語音品質都很好
- **deep-translator 而非 DeepL**：不需額外 API key，Google Translate 中日互譯品質足夠
- **音檔用 LINE AudioMessage 回傳**：使用者可以直接播放給日本人聽

## LINE SDK Patterns (v3)

沿用 cpbl-dashboard 的 LINE Bot SDK v3 模式：

```python
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi
from linebot.v3.webhooks import MessageEvent, TextMessageContent, AudioMessageContent
```

- 接收用 `WebhookHandler` + event decorator
- 發送用 `ApiClient` context manager + `MessagingApi`
- 語音訊息需額外處理 `AudioMessageContent`
