import json
import numpy as np
import onnxruntime
from transformers import CLIPTokenizer
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Используем те же ONNX + токенизатор, что и в app.py
ORT_SESSION = onnxruntime.InferenceSession(
    os.path.join(BASE_DIR, "clip-text-onnx", "model.onnx"),
    providers=["CPUExecutionProvider"]
)
CLIP_MAX_LENGTH = 77
TOKENIZER = CLIPTokenizer.from_pretrained("openai/clip-vit-base-patch32")

# Загружаем теги
with open("avatars.json", "r", encoding="utf-8") as f:
    avatars = json.load(f)

result = {}
for filename, tags in avatars.items():
    print(f"Processing: {filename} -> {tags[:50]}...")

    tokens = TOKENIZER(
        tags,
        padding="max_length",
        max_length=CLIP_MAX_LENGTH,
        truncation=True,
        return_tensors="np"
    )

    input_ids = tokens["input_ids"].astype(np.int64)
    attention_mask = tokens["attention_mask"].astype(np.int64)

    text_emb = ORT_SESSION.run(None, {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
    })[0]

    # Нормализация
    norm = np.linalg.norm(text_emb, axis=-1, keepdims=True)
    text_emb = text_emb / norm

    result[filename] = text_emb[0].tolist()

with open("vectors.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False)

print(f"\n✅ vectors.json готов: {len(result)} записей")