import os
import json
import shutil
import torch

from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer
from onnxruntime.quantization import quantize_dynamic, QuantType

MODEL_ID = "sentence-transformers/clip-ViT-B-32-multilingual-v1"

OUTPUT_DIR = "./clip-text-onnx"
MODEL_ONNX = os.path.join(OUTPUT_DIR, "model.onnx")

if os.path.exists(OUTPUT_DIR):
    shutil.rmtree(OUTPUT_DIR)

os.makedirs(OUTPUT_DIR, exist_ok=True)

print("1. Загрузка модели...")

st_model = SentenceTransformer(MODEL_ID)

transformer = st_model[0].auto_model
pooling = st_model[1]
dense = st_model[2]

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)

MAX_LENGTH = 128


class STTextEncoder(torch.nn.Module):
    def __init__(self, transformer, pooling, dense):
        super().__init__()

        self.transformer = transformer
        self.pooling = pooling
        self.dense = dense

    def forward(self, input_ids, attention_mask):
        outputs = self.transformer(
            input_ids=input_ids,
            attention_mask=attention_mask,
            return_dict=True,
        )

        token_embeddings = outputs.last_hidden_state

        mask = attention_mask.unsqueeze(-1).float()

        summed = (token_embeddings * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1e-9)

        mean_embeddings = summed / counts

        text_embeddings = self.dense.activation_function(
            self.dense.linear(mean_embeddings)
        )

        return torch.nn.functional.normalize(
            text_embeddings,
            p=2,
            dim=-1,
        )


model = STTextEncoder(
    transformer,
    pooling,
    dense,
)

model.eval()

dummy = tokenizer(
    ["test"],
    padding="max_length",
    truncation=True,
    max_length=MAX_LENGTH,
    return_tensors="pt"
)

print("2. Экспорт ONNX...")

torch.onnx.export(
    model,
    (
        dummy["input_ids"],
        dummy["attention_mask"],
    ),
    MODEL_ONNX,
    input_names=[
        "input_ids",
        "attention_mask",
    ],
    output_names=[
        "embeddings",
    ],
    dynamic_axes={
        "input_ids": {
            0: "batch_size",
            1: "sequence_length",
        },
        "attention_mask": {
            0: "batch_size",
            1: "sequence_length",
        },
        "embeddings": {
            0: "batch_size",
        },
    },
    opset_version=18,
    dynamo=False
)

print(
    f"ONNX size: "
    f"{os.path.getsize(MODEL_ONNX)/1024**2:.1f} MB"
)

print("3. INT8 quantization...")

quantize_dynamic(
    MODEL_ONNX,
    MODEL_ONNX,
    weight_type=QuantType.QInt8,
)

print(
    f"INT8 size: "
    f"{os.path.getsize(MODEL_ONNX)/1024**2:.1f} MB"
)

print("4. Сохраняем tokenizer.json")

tokenizer.save_pretrained(OUTPUT_DIR)

with open(
    os.path.join(OUTPUT_DIR, "config.json"),
    "w",
    encoding="utf-8",
) as f:
    json.dump(
        {
            "model_type": "clip_multilingual",
            "max_length": MAX_LENGTH,
            "embedding_size": 512,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )

print("✅ Готово")