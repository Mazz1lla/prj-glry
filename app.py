#!/usr/bin/env python3
"""
Avatar Tag Editor PRO — веб-версия
====================================
Локальный Flask-сервер + браузерный интерфейс.
Запуск: python app.py, затем открыть http://127.0.0.1:5057

Зависимости: pip install flask requests pillow
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory, abort
from PIL import Image

# --------------------------------------------------------------------------
# Константы
# --------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".avatar_tagger_config.json")
DEFAULT_JSON_NAME = "avatars.json"
HASH_SIDECAR_NAME = ".avatar_hashes.json"
PRESETS_NAME = ".bucket_presets.json"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024
REQUEST_TIMEOUT = 12
BUCKET_FILE_RE = re.compile(r"^(.+)_(\d+)\.(\w+)$", re.IGNORECASE)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"}

app = Flask(__name__)

# --------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------
def sanitize_bucket_name(name: str) -> str:
    name = (name or "").strip().lower()
    name = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "misc"


def sanitize_filename(name: str) -> str:
    name = os.path.basename((name or "").strip())
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name


def md5_of_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def is_roughly_square(width: int, height: int, tolerance: float = 0.12) -> bool:
    if width <= 0 or height <= 0:
        return False
    return abs(width - height) / max(width, height) <= tolerance


def guess_extension(content_type: str, url: str) -> str:
    content_type = (content_type or "").lower()
    mapping = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
    }
    if content_type in mapping:
        return mapping[content_type]
    m = re.search(r"\.(jpg|jpeg|png|webp|gif|bmp)(?:$|\?)", url, re.IGNORECASE)
    if m:
        ext = m.group(1).lower()
        return ".jpg" if ext == "jpeg" else f".{ext}"
    return ".jpg"


def list_buckets_from_folder(folder: str) -> list[str]:
    if not folder or not os.path.isdir(folder):
        return []
    buckets = set()
    for fname in os.listdir(folder):
        m = BUCKET_FILE_RE.match(fname)
        if m:
            buckets.add(m.group(1).lower())
    return sorted(buckets)


def next_index_for_bucket(folder: str, bucket: str) -> int:
    """Единственный источник истины для нумерации файлов бакета."""
    if not os.path.isdir(folder):
        return 1
    pattern = re.compile(rf"^{re.escape(bucket)}_(\d+)\.\w+$", re.IGNORECASE)
    max_n = 0
    for fname in os.listdir(folder):
        m = pattern.match(fname)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def atomic_write_json(path: str, data: dict) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def read_json_flexible(path: str):
    if not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1251"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, UnicodeError):
            continue
    return None


# --------------------------------------------------------------------------
# Состояние (одна рабочая папка за раз — инструмент для личного использования)
# --------------------------------------------------------------------------
class Store:
    def __init__(self):
        self.folder = ""
        self.json_path = ""
        self.data: dict[str, str] = {}
        self.hash_path = ""
        self.hash_index: dict[str, str] = {}
        self.presets_path = ""
        self.presets: dict[str, str] = {}
        self.lock = threading.Lock()

    def set_folder(self, folder: str):
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            raise ValueError(f"Папка не найдена: {folder}")
        self.folder = folder
        candidate = os.path.join(folder, DEFAULT_JSON_NAME)
        legacy = os.path.join(folder, "metadata.json")
        self.json_path = candidate
        raw = read_json_flexible(candidate)
        if raw is None and os.path.exists(legacy):
            raw = read_json_flexible(legacy)
        self.data = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                self.data[k] = " ".join(str(x) for x in v) if isinstance(v, list) else str(v)

        self.hash_path = os.path.join(folder, HASH_SIDECAR_NAME)
        raw_hash = read_json_flexible(self.hash_path)
        self.hash_index = raw_hash if isinstance(raw_hash, dict) else {}

        self.presets_path = os.path.join(folder, PRESETS_NAME)
        raw_presets = read_json_flexible(self.presets_path)
        if isinstance(raw_presets, dict):
            # поддержка старого формата {"presets": {...}, "auto": {...}}
            self.presets = raw_presets.get("presets", raw_presets) if "presets" in raw_presets else raw_presets
        else:
            self.presets = {}
        _save_config(folder)

    def save_data(self):
        atomic_write_json(self.json_path, self.data)

    def save_hash(self):
        atomic_write_json(self.hash_path, self.hash_index)

    def save_presets(self):
        atomic_write_json(self.presets_path, self.presets)

    def bucket_of(self, filename: str) -> str:
        m = BUCKET_FILE_RE.match(filename)
        return m.group(1).lower() if m else ""


store = Store()
search_cache: dict[str, bytes] = {}  # id -> raw image bytes, per search session


def _save_config(folder: str):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"working_folder": folder}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _load_config() -> str:
    raw = read_json_flexible(CONFIG_FILE)
    if isinstance(raw, dict):
        return raw.get("working_folder", "")
    return ""


def require_folder():
    if not store.folder:
        abort(400, description="Рабочая папка не выбрана")


def safe_path(filename: str) -> str:
    """Не даём выйти за пределы рабочей папки."""
    filename = sanitize_filename(filename)
    full = os.path.abspath(os.path.join(store.folder, filename))
    if not full.startswith(os.path.abspath(store.folder) + os.sep):
        abort(400, description="Некорректное имя файла")
    return full


# --------------------------------------------------------------------------
# Роуты: страница
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(os.path.join(APP_DIR, "templates"), "index.html")


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory(os.path.join(APP_DIR, "static"), p)


# --------------------------------------------------------------------------
# Роуты: папка / состояние
# --------------------------------------------------------------------------
@app.route("/api/state")
def api_state():
    last = _load_config() if not store.folder else store.folder
    if not store.folder and last and os.path.isdir(last):
        try:
            store.set_folder(last)
        except ValueError:
            pass
    return jsonify({
        "folder": store.folder,
        "json_path": store.json_path,
        "count": len(store.data),
        "buckets": list_buckets_from_folder(store.folder),
    })


@app.route("/api/folder", methods=["POST"])
def api_set_folder():
    body = request.get_json(force=True)
    folder = body.get("folder", "")
    try:
        store.set_folder(folder)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return api_state()


@app.route("/api/browse")
def api_browse():
    path = request.args.get("path") or os.path.expanduser("~")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        path = os.path.expanduser("~")
    try:
        entries = sorted(
            e for e in os.listdir(path)
            if os.path.isdir(os.path.join(path, e)) and not e.startswith(".")
        )
    except OSError:
        entries = []
    parent = os.path.dirname(path) if path != os.path.abspath(os.sep) else None
    return jsonify({"path": path, "parent": parent, "dirs": entries})


# --------------------------------------------------------------------------
# Роуты: файлы / изображения
# --------------------------------------------------------------------------
@app.route("/api/files")
def api_files():
    require_folder()
    mode = request.args.get("mode", "untagged")
    all_files = sorted(f for f in os.listdir(store.folder) if f.lower().endswith(IMAGE_EXTS))
    if mode == "untagged":
        files = [f for f in all_files if f not in store.data]
    else:
        files = all_files
    return jsonify({"files": files, "total_all": len(all_files), "total_tagged": len(store.data)})


@app.route("/api/image/<path:filename>")
def api_image(filename):
    require_folder()
    full = safe_path(filename)
    if not os.path.exists(full):
        abort(404)
    return send_file(full)


@app.route("/api/item/<path:filename>")
def api_item_get(filename):
    require_folder()
    bucket = store.bucket_of(filename)
    return jsonify({
        "filename": filename,
        "tags": store.data.get(filename, ""),
        "bucket": bucket,
        "preset": store.presets.get(bucket, "") if bucket else "",
        "has_tags": filename in store.data,
    })


@app.route("/api/item/<path:filename>", methods=["POST"])
def api_item_save(filename):
    require_folder()
    full = safe_path(filename)
    if not os.path.exists(full):
        abort(404)
    body = request.get_json(force=True)
    tags = (body.get("tags") or "").strip()
    if not tags:
        return jsonify({"error": "Пустые теги"}), 400
    store.data[filename] = tags
    store.save_data()
    return jsonify({"ok": True})


@app.route("/api/item/<path:filename>", methods=["DELETE"])
def api_item_delete(filename):
    require_folder()
    full = safe_path(filename)
    try:
        if os.path.exists(full):
            os.remove(full)
    except OSError as e:
        return jsonify({"error": str(e)}), 400
    store.data.pop(filename, None)
    store.save_data()
    for h, fn in list(store.hash_index.items()):
        if fn == filename:
            del store.hash_index[h]
    store.save_hash()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Роуты: переименование (единая, ИСПРАВЛЕННАЯ логика нумерации бакетов)
# --------------------------------------------------------------------------
@app.route("/api/rename", methods=["POST"])
def api_rename():
    require_folder()
    body = request.get_json(force=True)
    old_filename = sanitize_filename(body.get("filename", ""))
    bucket = sanitize_bucket_name(body.get("bucket", ""))
    if not old_filename or not bucket:
        return jsonify({"error": "Не указан файл или бакет"}), 400

    old_path = safe_path(old_filename)
    if not os.path.exists(old_path):
        abort(404)

    ext = os.path.splitext(old_filename)[1] or ".jpg"

    # ВСЕГДА вычисляем следующий свободный номер для бакета — независимо от
    # того, был ли файл уже в формате bucket_N.ext или имел произвольное имя.
    # Это исправляет баг, из-за которого файлы с "чужими" именами переименовывались
    # без номера и затирали друг друга.
    new_index = next_index_for_bucket(store.folder, bucket)
    new_filename = f"{bucket}_{new_index}{ext}"
    new_path = safe_path(new_filename)

    if os.path.exists(new_path):
        return jsonify({"error": f"Файл {new_filename} уже существует (гонка имён)"}), 409

    if old_filename == new_filename:
        return jsonify({"filename": new_filename, "unchanged": True})

    os.rename(old_path, new_path)
    tags = store.data.pop(old_filename, "")
    if tags:
        store.data[new_filename] = tags
        store.save_data()
    for h, fn in list(store.hash_index.items()):
        if fn == old_filename:
            store.hash_index[h] = new_filename
    store.save_hash()

    return jsonify({"filename": new_filename})


@app.route("/api/rename_free", methods=["POST"])
def api_rename_free():
    require_folder()
    body = request.get_json(force=True)
    old_filename = sanitize_filename(body.get("filename", ""))
    new_filename = sanitize_filename(body.get("new_name", ""))
    if not old_filename or not new_filename:
        return jsonify({"error": "Не указано имя"}), 400
    if "." not in new_filename:
        new_filename += os.path.splitext(old_filename)[1] or ".jpg"

    old_path = safe_path(old_filename)
    new_path = safe_path(new_filename)
    if not os.path.exists(old_path):
        abort(404)
    if old_filename == new_filename:
        return jsonify({"filename": new_filename, "unchanged": True})
    if os.path.exists(new_path):
        return jsonify({"error": f"Файл {new_filename} уже существует"}), 409

    os.rename(old_path, new_path)
    tags = store.data.pop(old_filename, "")
    if tags:
        store.data[new_filename] = tags
        store.save_data()
    for h, fn in list(store.hash_index.items()):
        if fn == old_filename:
            store.hash_index[h] = new_filename
    store.save_hash()
    return jsonify({"filename": new_filename})


# --------------------------------------------------------------------------
# Роуты: автоподсказки тегов
# --------------------------------------------------------------------------
@app.route("/api/suggestions")
def api_suggestions():
    require_folder()
    bucket = (request.args.get("bucket") or "").lower()
    prefix = (request.args.get("prefix") or "").strip().lower()
    limit = int(request.args.get("limit", 30))

    global_freq: dict[str, int] = {}
    bucket_freq: dict[str, int] = {}
    for fname, tags_str in store.data.items():
        f_bucket = store.bucket_of(fname)
        for tok in tags_str.split():
            global_freq[tok] = global_freq.get(tok, 0) + 1
            if bucket and f_bucket == bucket:
                bucket_freq[tok] = bucket_freq.get(tok, 0) + 1

    candidates = []
    for tok, gfreq in global_freq.items():
        if prefix and not tok.lower().startswith(prefix):
            continue
        in_bucket = tok in bucket_freq
        freq = bucket_freq.get(tok, 0) + gfreq  # частота в бакете важнее, но общая тоже учитывается
        candidates.append({
            "tag": tok,
            "freq": bucket_freq.get(tok, gfreq),
            "in_bucket": in_bucket,
            "_sort": (0 if in_bucket else 1, -freq, tok),
        })
    candidates.sort(key=lambda c: c["_sort"])
    for c in candidates:
        del c["_sort"]
    return jsonify({"suggestions": candidates[:limit]})


# --------------------------------------------------------------------------
# Роуты: пресеты бакетов (упрощено: один пресет = базовые теги бакета)
# --------------------------------------------------------------------------
@app.route("/api/presets")
def api_presets_list():
    require_folder()
    return jsonify({"presets": store.presets})


@app.route("/api/presets/<bucket>", methods=["POST"])
def api_presets_save(bucket):
    require_folder()
    bucket = sanitize_bucket_name(bucket)
    body = request.get_json(force=True)
    tags = (body.get("tags") or "").strip()
    if tags:
        store.presets[bucket] = tags
    else:
        store.presets.pop(bucket, None)
    store.save_presets()
    return jsonify({"ok": True, "presets": store.presets})


@app.route("/api/presets/<bucket>", methods=["DELETE"])
def api_presets_delete(bucket):
    require_folder()
    bucket = sanitize_bucket_name(bucket)
    store.presets.pop(bucket, None)
    store.save_presets()
    return jsonify({"ok": True})


@app.route("/api/buckets")
def api_buckets():
    require_folder()
    buckets = list_buckets_from_folder(store.folder)
    counts = {}
    for f in os.listdir(store.folder):
        b = store.bucket_of(f)
        if b:
            counts[b] = counts.get(b, 0) + 1
    return jsonify({"buckets": [{"name": b, "count": counts.get(b, 0), "preset": store.presets.get(b, "")} for b in buckets]})


# --------------------------------------------------------------------------
# Поиск изображений (парсинг HTML — хрупко, движки могут менять вёрстку)
# --------------------------------------------------------------------------
def search_google_images(query: str, page: int, per_page: int):
    start = page * per_page
    url = f"https://www.google.com/search?q={quote(query)}&tbm=isch&tbs=iar:s&hl=ru&start={start}"
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = resp.text
    low = text.lower()
    if "captcha" in low or "detected unusual traffic" in low:
        raise RuntimeError("Google показал капчу — попробуйте позже.")
    thumbs = re.findall(r'"(https://encrypted-tbn0\.gstatic\.com/images\?[^"]+)"', text)
    seen, result = set(), []
    for u in thumbs:
        if u not in seen:
            seen.add(u)
            result.append({"url": u, "source": "google"})
    return result[:per_page]


def search_yandex_images(query: str, page: int, per_page: int):
    url = f"https://yandex.ru/images/search?text={quote(query)}&p={page}"
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = resp.text
    low = text.lower()
    if "showcaptcha" in low or "confirm you are not a robot" in low:
        raise RuntimeError("Yandex показал капчу — попробуйте позже.")
    m = re.search(r'<div[^>]+class="Root"[^>]+id="ajax-content"[^>]+data-state="([^"]+)"', text)
    if not m:
        raw_urls = re.findall(r'"img_href":"(https?:[^"]+)"', text)
        raw_urls = [u.replace("\\u002F", "/").replace("\\/", "/") for u in raw_urls]
        return [{"url": u, "source": "yandex"} for u in raw_urls[:per_page]]
    state_raw = m.group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&#x27;", "'")
    state = json.loads(state_raw)
    result = []
    entities = state.get("initialState", {}).get("serpList", {}).get("items", {}).get("entities", {})
    for _, item in entities.items():
        href = item.get("image", {}).get("origin", {}).get("url") or item.get("viewerImage", {}).get("url")
        if href:
            if href.startswith("//"):
                href = "https:" + href
            result.append({"url": href, "source": "yandex"})
    return result[:per_page]


def fetch_image_bytes(url: str):
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    data = bytearray()
    for chunk in resp.iter_content(8192):
        data.extend(chunk)
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("Файл слишком большой")
    return bytes(data), content_type


@app.route("/api/search", methods=["POST"])
def api_search():
    require_folder()
    body = request.get_json(force=True)
    query = (body.get("query") or "").strip()
    engine = body.get("engine", "google")
    page = int(body.get("page", 0))
    count = int(body.get("count", 20))
    square_only = bool(body.get("square_only", True))
    if not query:
        return jsonify({"error": "Пустой запрос"}), 400

    engines = []
    if engine in ("google", "both"):
        engines.append("google")
    if engine in ("yandex", "both"):
        engines.append("yandex")
    per_engine = max(5, count // max(1, len(engines)))

    found = []
    errors = []
    for eng in engines:
        try:
            if eng == "google":
                found.extend(search_google_images(query, page, per_engine))
            else:
                found.extend(search_yandex_images(query, page, per_engine))
        except (RuntimeError, requests.RequestException, json.JSONDecodeError) as e:
            errors.append(f"{eng}: {e}")

    results = []
    for f in found:
        try:
            data, ctype = fetch_image_bytes(f["url"])
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                if square_only and not is_roughly_square(w, h):
                    continue
                thumb = im.copy()
                thumb.thumbnail((160, 160))
                buf = io.BytesIO()
                thumb.convert("RGB").save(buf, format="JPEG", quality=80)
                thumb_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            continue
        rid = uuid.uuid4().hex
        search_cache[rid] = data
        results.append({"id": rid, "width": w, "height": h, "source": f["source"], "thumb": thumb_b64})
        time.sleep(0.02)

    return jsonify({"results": results, "errors": errors})


@app.route("/api/download", methods=["POST"])
def api_download():
    require_folder()
    body = request.get_json(force=True)
    ids = body.get("ids") or []
    bucket = sanitize_bucket_name(body.get("bucket", ""))
    tag_string = (body.get("tags") or bucket).strip()
    if not ids or not bucket:
        return jsonify({"error": "Не выбраны файлы или не указан бакет"}), 400

    next_n = next_index_for_bucket(store.folder, bucket)
    saved = dup = failed = 0
    for rid in ids:
        data = search_cache.get(rid)
        if not data:
            failed += 1
            continue
        file_hash = md5_of_bytes(data)
        if file_hash in store.hash_index:
            dup += 1
            continue
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt = (im.format or "JPEG").lower()
        except Exception:
            fmt = "jpeg"
        ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif", "bmp": ".bmp"}.get(fmt, ".jpg")
        filename = f"{bucket}_{next_n}{ext}"
        full_path = safe_path(filename)
        try:
            with open(full_path, "wb") as f:
                f.write(data)
        except OSError:
            failed += 1
            continue
        store.data[filename] = tag_string
        store.hash_index[file_hash] = filename
        next_n += 1
        saved += 1

    store.save_data()
    store.save_hash()
    return jsonify({"saved": saved, "duplicates": dup, "failed": failed})


if __name__ == "__main__":
    print("Avatar Tag Editor PRO — открой http://127.0.0.1:5057 в браузере")
    app.run(host="127.0.0.1", port=5057, debug=False)
