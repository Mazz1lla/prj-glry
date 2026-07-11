import json
import torch
from PIL import Image
import open_clip


REPO = "./"


model, _, preprocess = open_clip.create_model_and_transforms(
    "ViT-B-32",
    pretrained="laion2b_s34b_b79k"
)


with open("avatars.json", "r", encoding="utf-8") as f:
    avatars = json.load(f)


result = {}


for filename in avatars:

    print("Processing:", filename)

    image = preprocess(
        Image.open(
            f"{REPO}/{filename}"
        ).convert("RGB")
    ).unsqueeze(0)


    with torch.no_grad():
        vector = model.encode_image(image)

    vector /= vector.norm(
        dim=-1,
        keepdim=True
    )


    result[filename] = vector[0].tolist()


with open(
    "vectors.json",
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        result,
        f
    )


print("DONE")