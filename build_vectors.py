import os
import json
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

MODEL_DIR = os.path.join(
    BASE_DIR,
    "clip-text-onnx"
)

SESSION = ort.InferenceSession(
    os.path.join(
        MODEL_DIR,
        "model.onnx"
    ),
    providers=[
        "CPUExecutionProvider"
    ]
)

TOKENIZER = AutoTokenizer.from_pretrained(
    MODEL_DIR
)

MAX_LENGTH = 128

with open(
    "avatars.json",
    "r",
    encoding="utf-8"
) as f:
    avatars = json.load(f)

result = {}

for filename, tags in avatars.items():

    if isinstance(tags, list):
        text = ", ".join(tags)
    else:
        text = str(tags)

    print(
        f"Processing: {filename}"
    )

    tokens = TOKENIZER(
        text,
        padding="max_length",
        truncation=True,
        max_length=MAX_LENGTH,
        return_tensors="np"
    )

    embedding = SESSION.run(
        None,
        {
            "input_ids":
                tokens["input_ids"].astype(np.int64),

            "attention_mask":
                tokens["attention_mask"].astype(np.int64),
        }
    )[0][0]

    embedding = embedding.astype(
        np.float32
    )

    embedding /= (
        np.linalg.norm(embedding)
        + 1e-12
    )

    result[filename] = embedding.tolist()

with open(
    "vectors.json",
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        result,
        f,
        ensure_ascii=False
    )

print(
    f"\n✅ vectors.json готов: "
    f"{len(result)} записей"
)