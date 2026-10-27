"""
media.py — «движок» асинхронного сайта: библиотека источников, загрузка, метрики.

Здесь живут те же паттерны asyncio, что и в CLI-демо (sync / gather /
as_completed / asyncio.wait с таймаутом), только вместо time.sleep и
asyncio.sleep с фиксированными задержками мы либо качаем реальные файлы
(Giphy, test-videos.co.uk, MDN), либо имитируем загрузку в демо-режиме
(asyncio.sleep + локальные заглушки, сгенерированные Pillow/ffmpeg).
---
AI style comments?
"""

from __future__ import annotations

import asyncio
import io
import math
import os
import random
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent
SIM_DIR = BASE_DIR / "sim_assets"
SIM_DIR.mkdir(exist_ok=True)

USER_AGENT = "AsyncMediaFlaskDemo/1.0 (educational demo)"
HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

# Принудительный демо-режим: FORCE_SIM=1 ./run  (без обращений в сеть)
FORCE_SIM = os.environ.get("FORCE_SIM", "").strip() in ("1", "true", "yes")


def headers() -> dict:
    return {"User-Agent": USER_AGENT}


# --------------------------------------------------------------------------
# Библиотека медиа. size_hint_mb и url сняты реальными запросами;
# sim_delay — «плановое ожидание» в демо-режиме (аналог delay из CLI-примера).
# Элемент с большим sim_delay (6.5 c) специально нужен для демо таймаута.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MediaItem:
    id: str
    kind: str            # "gif" | "video"
    title: str
    url: str
    sim_delay: float
    size_hint_mb: float


LIBRARY: list[MediaItem] = [
    # --- GIF (Giphy CDN) ---
    MediaItem("gif-aurora", "gif", "Гифка «Aurora»",
              "https://media.giphy.com/media/JIX9t2j0ZTN9S/giphy.gif", 1.6, 1.8),
    MediaItem("gif-comet", "gif", "Гифка «Comet»",
              "https://media.giphy.com/media/3oriO0OEd9QIDdllqo/giphy.gif", 1.4, 1.5),
    MediaItem("gif-nebula", "gif", "Гифка «Nebula»",
              "https://media.giphy.com/media/3o7TKr3nzbh5WgCFxe/giphy.gif", 2.0, 4.2),
    MediaItem("gif-pulse", "gif", "Гифка «Pulse»",
              "https://media.giphy.com/media/26ufdipQqU2lhNA4g/giphy.gif", 0.8, 0.5),
    MediaItem("gif-quasar", "gif", "Гифка «Quasar»",
              "https://media.giphy.com/media/l3q2K5jinAlChoCLS/giphy.gif", 1.1, 0.9),
    MediaItem("gif-vega", "gif", "Гифка «Vega»",
              "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif", 2.2, 4.7),
    # --- Видео (test-videos.co.uk, CC; MDN sample, CC0) ---
    MediaItem("vid-flower", "video", "Цветок (MDN, 1.1 МБ)",
              "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4", 1.2, 1.1),
    MediaItem("vid-bbb-360", "video", "Big Buck Bunny 360p (1 МБ)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4", 1.4, 1.0),
    MediaItem("vid-bbb-720s", "video", "Big Buck Bunny 720p (2 МБ)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_2MB.mp4", 1.8, 2.0),
    MediaItem("vid-bbb-720m", "video", "Big Buck Bunny 720p (5 МБ)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_5MB.mp4", 2.4, 5.0),
    MediaItem("vid-jelly-720", "video", "Jellyfish 720p (5 МБ)",
              "https://test-videos.co.uk/vids/jellyfish/mp4/h264/720/Jellyfish_720_10s_5MB.mp4", 2.6, 5.2),
    MediaItem("vid-bbb-1080m", "video", "Big Buck Bunny 1080p (10 МБ)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_10MB.mp4", 3.4, 10.5),
    MediaItem("vid-sintel-1080", "video", "Sintel 1080p (20 МБ)",
              "https://test-videos.co.uk/vids/sintel/mp4/h264/1080/Sintel_1080_10s_20MB.mp4", 4.6, 21.0),
    MediaItem("vid-bbb-heavy", "video", "Тяжёлое: Big Buck Bunny 1080p (30 МБ)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_30MB.mp4", 6.5, 30.7),
]


# --------------------------------------------------------------------------
# Режим работы: реальные загрузки ("real") или локальная имитация ("sim")
# --------------------------------------------------------------------------

_mode_cache: str | None = None
_mode_lock = threading.Lock()


def _probe_internet() -> bool:
    try:
        r = httpx.head(
            "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4",
            headers=headers(), timeout=httpx.Timeout(6.0, connect=4.0), follow_redirects=True,
        )
        return r.status_code < 500
    except Exception:
        return False


def detect_mode() -> str:
    global _mode_cache
    if FORCE_SIM:
        return "sim"
    with _mode_lock:
        if _mode_cache is None:
            _mode_cache = "real" if _probe_internet() else "sim"
        return _mode_cache


def is_simulated() -> bool:
    return detect_mode() == "sim"


def select_items(kind: str, count: int) -> list[MediaItem]:
    """Выбрать элементы библиотеки: gif / video / mix."""
    pool = [it for it in LIBRARY if kind == "mix" or it.kind == kind]
    random.shuffle(pool)
    return pool[: max(1, min(count, len(pool)))]


# --------------------------------------------------------------------------
# Временное хранилище скачанных байтов (раздаётся через /media/<req>/<item>)
# --------------------------------------------------------------------------

_STORE: dict[tuple[str, str], dict] = {}
_STORE_LOCK = threading.Lock()
_STORE_TTL = 20 * 60                 # секунды
_STORE_MAX_BYTES = 250 * 1024 * 1024  # 250 МБ


def _purge_locked(now: float) -> None:
    dead = [k for k, v in _STORE.items() if now - v["ts"] > _STORE_TTL]
    for k in dead:
        del _STORE[k]


def store_put(req_id: str, item_id: str, payload: bytes, content_type: str, filename: str) -> None:
    now = time.time()
    with _STORE_LOCK:
        _purge_locked(now)
        _STORE[(req_id, item_id)] = {
            "bytes": payload, "content_type": content_type,
            "filename": filename, "ts": now,
        }
        total = sum(len(v["bytes"]) for v in _STORE.values())
        oldest = sorted(_STORE.keys(), key=lambda k: _STORE[k]["ts"])
        for k in oldest:
            if total <= _STORE_MAX_BYTES:
                break
            if k == (req_id, item_id):
                continue
            total -= len(_STORE[k]["bytes"])
            del _STORE[k]


def store_get(req_id: str, item_id: str) -> dict | None:
    with _STORE_LOCK:
        entry = _STORE.get((req_id, item_id))
        if entry:
            entry["ts"] = time.time()
        return entry


# --------------------------------------------------------------------------
# Результаты и метрики (аналог DemoResult / print_metrics из CLI-примера)
# --------------------------------------------------------------------------

@dataclass
class FetchResult:
    req_id: str
    index: int
    item_id: str
    title: str
    kind: str
    ok: bool
    elapsed: float          # собственное время загрузки (без ожидания семафора)
    size_bytes: int
    content_type: str
    media_url: str = ""
    error: str = ""
    simulated: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Metrics:
    mode: str
    completed: int
    total: int
    elapsed: float
    sequential_estimate: float
    time_saved: float
    throughput: float
    bytes_received: int
    simulated: bool
    cancelled: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def compute_metrics(mode: str, results: list[FetchResult], total: int,
                    elapsed: float, cancelled: int = 0) -> Metrics:
    ok = [r for r in results if r.ok]
    sequential = sum(r.elapsed for r in results)   # оценка «если бы качали по очереди»
    saved = max(0.0, sequential - elapsed)         # sync-режим даёт ~0, не уходим в минус
    if abs(saved) < 0.01:
        saved = 0.0
    return Metrics(
        mode=mode, completed=len(ok), total=total, elapsed=elapsed,
        sequential_estimate=sequential, time_saved=saved,
        throughput=(len(ok) / elapsed if elapsed > 0 else 0.0),
        bytes_received=sum(r.size_bytes for r in ok),
        simulated=(results[0].simulated if results else is_simulated()),
        cancelled=cancelled,
    )


# --------------------------------------------------------------------------
# Локальные заглушки для демо-режима (Pillow-гифки, mp4 через ffmpeg)
# --------------------------------------------------------------------------

_SIM_CACHE: dict[str, tuple[bytes, str]] = {}
_SIM_LOCK = threading.Lock()
FFMPEG = shutil.which("ffmpeg")


def _make_sim_gif(item: MediaItem) -> bytes:
    """Анимированная гифка-заглушка: мяч прыгает по сетке."""
    from PIL import Image, ImageDraw

    w, h, n = 480, 270, 24
    palette = [(34, 211, 238), (167, 139, 250), (251, 146, 60),
               (74, 222, 128), (244, 114, 182), (250, 204, 21)]
    color = palette[sum(ord(c) for c in item.id) % len(palette)]
    frames = []
    for i in range(n):
        t = i / n
        img = Image.new("RGB", (w, h), (13, 20, 38))
        d = ImageDraw.Draw(img)
        for gx in range(0, w, 40):
            d.line([(gx, 0), (gx, h)], fill=(22, 32, 58))
        for gy in range(0, h, 40):
            d.line([(0, gy), (w, gy)], fill=(22, 32, 58))
        cx = 60 + t * (w - 120)
        cy = h / 2 + math.sin(t * 2 * math.pi) * 60
        r = 28
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        d.text((14, 12), f"SIM {item.id} {i + 1}/{n}", fill=(148, 163, 184))
        frames.append(img)
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True,
                   append_images=frames[1:], duration=90, loop=0, optimize=True)
    return buf.getvalue()


def _make_sim_mp4(item: MediaItem) -> bytes:
    """Небольшой mp4-заглушка через ffmpeg testsrc2 (если ffmpeg установлен)."""
    path = SIM_DIR / f"{item.id}.mp4"
    if not path.exists():
        freq = 220 + (sum(ord(c) for c in item.id) % 6) * 110
        subprocess.run(
            ["ffmpeg", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=24",
             "-f", "lavfi", "-i", f"sine=frequency={freq}",
             "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(path)],
            check=True, capture_output=True, timeout=120,
        )
    return path.read_bytes()


def placeholder_payload(item: MediaItem) -> tuple[bytes, str]:
    """(байты, content_type) заглушки для демо-режима, с кэшем в памяти."""
    with _SIM_LOCK:
        cached = _SIM_CACHE.get(item.id)
    if cached:
        return cached
    if item.kind == "gif":
        payload, ctype = _make_sim_gif(item), "image/gif"
    elif FFMPEG:
        try:
            payload, ctype = _make_sim_mp4(item), "video/mp4"
        except Exception:
            payload, ctype = _make_sim_gif(item), "image/gif"
    else:
        payload, ctype = _make_sim_gif(item), "image/gif"
    with _SIM_LOCK:
        _SIM_CACHE[item.id] = (payload, ctype)
    return payload, ctype


_warmed = False


def warm_sim_assets() -> None:
    """Заранее сгенерировать заглушки (в фоновом потоке), если мы в демо-режиме."""
    global _warmed
    if _warmed or not is_simulated():
        return
    _warmed = True

    def worker() -> None:
        for it in LIBRARY:
            try:
                placeholder_payload(it)
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()


# --------------------------------------------------------------------------
# Загрузка: асинхронная (httpx) и синхронная (requests) версии
# --------------------------------------------------------------------------

def _ext_for(content_type: str, kind: str) -> str:
    if content_type.startswith("image/gif"):
        return "gif"
    if "mp4" in content_type:
        return "mp4"
    return "gif" if kind == "gif" else "bin"


async def fetch_item(client: httpx.AsyncClient, item: MediaItem, req_id: str,
                     sem: asyncio.Semaphore, simulated: bool, idx: int = 0) -> FetchResult:
    """Одна «задача»: скачать элемент. Тайминги меряются после входа в семафор,
    чтобы сумма отдельных времён была честной оценкой последовательной работы."""
    async with sem:
        t0 = time.perf_counter()
        ok, err = True, ""
        payload, ctype = b"", ""
        try:
            if simulated:
                # имитация «сети»: спим sim_delay с небольшим джиттером
                await asyncio.sleep(item.sim_delay * random.uniform(0.9, 1.15))
                payload, ctype = placeholder_payload(item)
            else:
                resp = await client.get(item.url, follow_redirects=True)
                resp.raise_for_status()
                payload = resp.content
                ctype = resp.headers.get("content-type", "") or (
                    "image/gif" if item.kind == "gif" else "video/mp4")
        except Exception as e:  # noqa: BLE001 — показываем ошибку в UI
            ok, err = False, f"{type(e).__name__}: {e}"
        elapsed = time.perf_counter() - t0
        res = FetchResult(
            req_id=req_id, index=idx, item_id=item.id, title=item.title,
            kind=item.kind, ok=ok, elapsed=elapsed, size_bytes=len(payload),
            content_type=ctype, error=err, simulated=simulated,
        )
        if ok and payload:
            ext = _ext_for(ctype, item.kind)
            store_put(req_id, item.id, payload, ctype, f"{item.id}.{ext}")
            res.media_url = f"/media/{req_id}/{item.id}"
        return res


def fetch_item_sync(item: MediaItem, req_id: str, simulated: bool, idx: int = 0) -> FetchResult:
    """Блокирующая версия для «sync»-демо: аналог time.sleep в CLI-примере."""
    import requests

    t0 = time.perf_counter()
    ok, err = True, ""
    payload, ctype = b"", ""
    try:
        if simulated:
            time.sleep(item.sim_delay * random.uniform(0.9, 1.15))
            payload, ctype = placeholder_payload(item)
        else:
            resp = requests.get(item.url, headers=headers(),
                                timeout=(10, 60), allow_redirects=True)
            resp.raise_for_status()
            payload = resp.content
            ctype = resp.headers.get("content-type", "") or (
                "image/gif" if item.kind == "gif" else "video/mp4")
    except Exception as e:  # noqa: BLE001
        ok, err = False, f"{type(e).__name__}: {e}"
    elapsed = time.perf_counter() - t0
    res = FetchResult(
        req_id=req_id, index=idx, item_id=item.id, title=item.title,
        kind=item.kind, ok=ok, elapsed=elapsed, size_bytes=len(payload),
        content_type=ctype, error=err, simulated=simulated,
    )
    if ok and payload:
        ext = _ext_for(ctype, item.kind)
        store_put(req_id, item.id, payload, ctype, f"{item.id}.{ext}")
        res.media_url = f"/media/{req_id}/{item.id}"
    return res


def new_req_id() -> str:
    return uuid.uuid4().hex[:10]
