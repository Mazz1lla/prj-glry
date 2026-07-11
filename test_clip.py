import json
from tokenizers import Tokenizer
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
tokenizer_path = os.path.join(BASE_DIR, "clip-text-onnx", "tokenizer.json")

# Загружаем токенизатор
tokenizer = Tokenizer.from_file(tokenizer_path)

# Получаем словарь
vocab = tokenizer.get_vocab()
print(f"Размер словаря: {len(vocab)}")

# Проверяем, есть ли русские токены
for text in ["cat", "кот", "собака", "anime", "аниме"]:
    encoded = tokenizer.encode(text)
    ids = encoded.ids[:5]
    tokens = [tokenizer.id_to_token(id) for id in ids]
    print(f"\n'{text}':")
    print(f"  ID: {ids}")
    print(f"  Токены: {tokens}")
    # Проверяем, все ли ID валидны
    for id in ids:
        if id != 0 and id != 1 and id != 2:
            try:
                token = tokenizer.id_to_token(id)
                if token is None:
                    print(f"  ⚠️ ID {id} отсутствует в словаре!")
            except:
                print(f"  ⚠️ Ошибка для ID {id}")