"""圖片文字辨識 (OCR) 模組 — 使用 Groq Vision API。"""

import base64
from groq import Groq
from config import GROQ_API_KEY


def image_to_text(image_path):
    """辨識圖片中的文字並回傳。支援菜單、路標、指示牌等。"""
    client = Groq(api_key=GROQ_API_KEY)

    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    # 用 Groq Vision 模型辨識圖片文字
    response = client.chat.completions.create(
        model="llama-3.2-90b-vision-preview",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "請辨識這張圖片中的所有文字。"
                            "只回傳辨識到的文字內容，不要加任何說明或解釋。"
                            "如果有多行文字，用換行分隔。"
                            "如果圖片中沒有文字，回傳「無法辨識文字」。"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                    },
                ],
            }
        ],
        max_tokens=1024,
    )

    return response.choices[0].message.content.strip()
