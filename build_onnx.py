import torch
from transformers import CLIPModel, CLIPTokenizer
from onnxruntime.quantization import quantize_dynamic, QuantType
import os, shutil, json

MODEL_ID = "sentence-transformers/clip-ViT-B-32-multilingual-v1"
TOKENIZER_ID = "openai/clip-vit-base-patch32"  # словарь 49408 токенов
OUTPUT_DIR = "./clip-text-onnx"
MODEL_ONNX = os.path.join(OUTPUT_DIR, "model.onnx")

if os.path.exists(OUTPUT_DIR):
    shutil.rmtree(OUTPUT_DIR)
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("1. Загрузка CLIP и токенизатора...")
model = CLIPModel.from_pretrained(MODEL_ID)
tokenizer = CLIPTokenizer.from_pretrained(TOKENIZER_ID)
model.eval()

max_length = model.config.text_config.max_position_embeddings  # 77

class TextEncoderWrapper(torch.nn.Module):
    def __init__(self, clip_model):
        super().__init__()
        self.clip_model = clip_model
    def forward(self, input_ids, attention_mask):
        return self.clip_model.get_text_features(
            input_ids=input_ids, attention_mask=attention_mask
        )

text_encoder = TextEncoderWrapper(model)
text_encoder.eval()

dummy = tokenizer(
    ["test"],
    padding="max_length",
    max_length=max_length,
    truncation=True,
    return_tensors="pt"
)

print("2. Экспорт в ONNX...")
torch.onnx.export(
    text_encoder,
    (dummy.input_ids, dummy.attention_mask),
    MODEL_ONNX,
    input_names=["input_ids", "attention_mask"],
    output_names=["text_embeds"],
    dynamic_axes={
        "input_ids":      {0: "batch_size"},
        "attention_mask": {0: "batch_size"},
    },
    opset_version=14,
)

print(f"   model.onnx: {os.path.getsize(MODEL_ONNX)/1024**2:.1f} MB")

print("3. INT8 квантизация...")
quantize_dynamic(MODEL_ONNX, MODEL_ONNX, weight_type=QuantType.QInt8)
print(f"   model.onnx (INT8): {os.path.getsize(MODEL_ONNX)/1024**2:.1f} MB")

print("4. Сохранение токенизатора (tokenizer.json)...")
tokenizer.backend_tokenizer.save(os.path.join(OUTPUT_DIR, "tokenizer.json"))

with open(os.path.join(OUTPUT_DIR, "config.json"), "w") as f:
    json.dump({"model_type": "clip", "max_length": max_length}, f)

# Удаляем лишние файлы
for fname in os.listdir(OUTPUT_DIR):
    if fname not in ("model.onnx", "tokenizer.json", "config.json"):
        os.remove(os.path.join(OUTPUT_DIR, fname))

print("✅ Готово! Файлы в clip-text-onnx/")