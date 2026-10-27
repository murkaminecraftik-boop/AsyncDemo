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
from typing import Callable, Optional

import httpx

BASE_DIR = Path(__file__).resolve().parent
SIM_DIR = BASE_DIR / "sim_assets"
SIM_DIR.mkdir(exist_ok=True)

USER_AGENT = "AsyncMediaFlaskDemo/2.0 (educational demo)"
HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
CHUNK_SIZE = 65536
RETRIES = 2

FORCE_SIM = os.environ.get("FORCE_SIM", "").strip() in ("1", "true", "yes")


def headers() -> dict:
    return {"User-Agent": USER_AGENT}



@dataclass(frozen=True)
class MediaItem:
    id: str
    kind: str
    title: str
    url: str
    sim_delay: float
    size_hint_mb: float


LIBRARY: list[MediaItem] = [
    MediaItem("gif-aurora", "gif", "Giphy JIX9t2j0ZTN9S",
              "https://media.giphy.com/media/JIX9t2j0ZTN9S/giphy.gif", 1.6, 1.8),
    MediaItem("gif-comet", "gif", "Giphy 3oriO0OEd9QIDdllqo",
              "https://media.giphy.com/media/3oriO0OEd9QIDdllqo/giphy.gif", 1.4, 1.5),
    MediaItem("gif-nebula", "gif", "Giphy 3o7TKr3nzbh5WgCFxe",
              "https://media.giphy.com/media/3o7TKr3nzbh5WgCFxe/giphy.gif", 2.0, 4.2),
    MediaItem("gif-pulse", "gif", "Giphy 26ufdipQqU2lhNA4g",
              "https://media.giphy.com/media/26ufdipQqU2lhNA4g/giphy.gif", 0.8, 0.5),
    MediaItem("gif-quasar", "gif", "Giphy l3q2K5jinAlChoCLS",
              "https://media.giphy.com/media/l3q2K5jinAlChoCLS/giphy.gif", 1.1, 0.9),
    MediaItem("gif-vega", "gif", "Giphy l0MYt5jPR6QX5pnqM",
              "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif", 2.2, 4.7),
    MediaItem("vid-flower", "video", "Flower (MDN sample, 1.1 MB)",
              "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4", 1.2, 1.1),
    MediaItem("vid-bbb-360", "video", "Big Buck Bunny 360p (1 MB)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4", 1.4, 1.0),
    MediaItem("vid-bbb-720s", "video", "Big Buck Bunny 720p (2 MB)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_2MB.mp4", 1.8, 2.0),
    MediaItem("vid-bbb-720m", "video", "Big Buck Bunny 720p (5 MB)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_5MB.mp4", 2.4, 5.0),
    MediaItem("vid-jelly-720", "video", "Jellyfish 720p (5 MB)",
              "https://test-videos.co.uk/vids/jellyfish/mp4/h264/720/Jellyfish_720_10s_5MB.mp4", 2.6, 5.2),
    MediaItem("vid-bbb-1080m", "video", "Big Buck Bunny 1080p (10 MB)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_10MB.mp4", 3.4, 10.5),
    MediaItem("vid-sintel-1080", "video", "Sintel 1080p (20 MB)",
              "https://test-videos.co.uk/vids/sintel/mp4/h264/1080/Sintel_1080_10s_20MB.mp4", 4.6, 21.0),
    MediaItem("vid-bbb-heavy", "video", "Big Buck Bunny 1080p (30 MB, heavy)",
              "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_30MB.mp4", 6.5, 30.7),
]


_mode_cache: Optional[str] = None
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
    pool = [it for it in LIBRARY if kind == "mix" or it.kind == kind]
    random.shuffle(pool)
    return pool[: max(1, min(count, len(pool)))]



_STORE: dict[tuple[str, str], dict] = {}
_STORE_LOCK = threading.Lock()
_STORE_TTL = 20 * 60
_STORE_MAX_BYTES = 250 * 1024 * 1024


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


def store_get(req_id: str, item_id: str) -> Optional[dict]:
    with _STORE_LOCK:
        entry = _STORE.get((req_id, item_id))
        if entry:
            entry["ts"] = time.time()
        return entry




@dataclass
class FetchResult:
    req_id: str
    index: int
    item_id: str
    title: str
    kind: str
    ok: bool
    elapsed: float
    size_bytes: int
    content_type: str
    total_bytes: int = 0
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
    sequential = sum(r.elapsed for r in results)
    saved = max(0.0, sequential - elapsed)
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

def _sniff(payload: bytes, ctype: str, fallback_kind: str) -> str:
    ctype = (ctype or "").split(";")[0].strip().lower()
    if ctype.startswith(("image/", "video/")):
        return ctype
    head = payload[:16]
    if head.startswith(b"GIF8"):
        return "image/gif"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "video/mp4"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    return ctype or ("image/gif" if fallback_kind == "gif" else "video/mp4")


def _ext_for(content_type: str, kind: str) -> str:
    if content_type.startswith("image/gif"):
        return "gif"
    if "mp4" in content_type:
        return "mp4"
    if content_type.startswith("image/"):
        return "img"
    return "gif" if kind == "gif" else "bin"


_SIM_CACHE: dict[str, tuple[bytes, str]] = {}
_SIM_LOCK = threading.Lock()
FFMPEG = shutil.which("ffmpeg")


def _make_sim_gif(item: MediaItem) -> bytes:
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



ProgressCB = Optional[Callable[[int, int], None]]


def _finalize(req_id: str, item: MediaItem, idx: int, elapsed: float, payload: bytes,
              ctype: str, simulated: bool) -> FetchResult:
    ctype = _sniff(payload, ctype, item.kind)
    res = FetchResult(
        req_id=req_id, index=idx, item_id=item.id, title=item.title,
        kind=item.kind, ok=True, elapsed=elapsed, size_bytes=len(payload),
        content_type=ctype, total_bytes=len(payload), simulated=simulated,
    )
    ext = _ext_for(ctype, item.kind)
    store_put(req_id, item.id, payload, ctype, f"{item.id}.{ext}")
    res.media_url = f"/media/{req_id}/{item.id}"
    return res


def _fail(req_id: str, item: MediaItem, idx: int, elapsed: float, err: str,
          simulated: bool) -> FetchResult:
    return FetchResult(
        req_id=req_id, index=idx, item_id=item.id, title=item.title,
        kind=item.kind, ok=False, elapsed=elapsed, size_bytes=0,
        content_type="", error=err, simulated=simulated,
    )


async def fetch_item(client: httpx.AsyncClient, item: MediaItem, req_id: str,
                     sem: asyncio.Semaphore, simulated: bool, idx: int = 0,
                     on_progress: ProgressCB = None) -> FetchResult:
    async with sem:
        t0 = time.perf_counter()

        if simulated:
            total = int(item.size_hint_mb * 1_000_000) or 1
            delay = item.sim_delay * random.uniform(0.9, 1.15)
            steps = 24
            for i in range(1, steps + 1):
                await asyncio.sleep(delay / steps)
                if on_progress:
                    on_progress(int(total * i / steps), total)
            try:
                payload, ctype = placeholder_payload(item)
            except Exception as e:
                return _fail(req_id, item, idx, time.perf_counter() - t0,
                             f"{type(e).__name__}: {e}", True)
            return _finalize(req_id, item, idx, time.perf_counter() - t0, payload, ctype, True)

        err = ""
        for attempt in range(1, RETRIES + 1):
            try:
                buf = bytearray()
                ctype, total = "", 0
                last_emit = 0.0
                async with client.stream("GET", item.url, follow_redirects=True) as resp:
                    if resp.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}", request=resp.request, response=resp)
                    ctype = resp.headers.get("content-type", "")
                    cl = resp.headers.get("content-length", "")
                    total = int(cl) if cl.isdigit() else int(item.size_hint_mb * 1_000_000)
                    async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                        buf.extend(chunk)
                        if on_progress:
                            now = time.perf_counter()
                            if now - last_emit > 0.35 or len(buf) >= total:
                                last_emit = now
                                on_progress(len(buf), total)
                if on_progress:
                    on_progress(len(buf), total)
                return _finalize(req_id, item, idx,
                                 time.perf_counter() - t0, bytes(buf), ctype, False)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                if attempt < RETRIES:
                    await asyncio.sleep(0.5)
        return _fail(req_id, item, idx, time.perf_counter() - t0, err, False)


def fetch_item_sync(item: MediaItem, req_id: str, simulated: bool, idx: int = 0) -> FetchResult:

    import requests

    t0 = time.perf_counter()
    if simulated:
        try:
            time.sleep(item.sim_delay * random.uniform(0.9, 1.15))
            payload, ctype = placeholder_payload(item)
        except Exception as e:
            return _fail(req_id, item, idx, time.perf_counter() - t0,
                         f"{type(e).__name__}: {e}", True)
        return _finalize(req_id, item, idx, time.perf_counter() - t0, payload, ctype, True)

    err = ""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(item.url, headers=headers(),
                                timeout=(10, 60), allow_redirects=True)
            resp.raise_for_status()
            return _finalize(req_id, item, idx, time.perf_counter() - t0,
                             resp.content, resp.headers.get("content-type", ""), False)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if attempt < RETRIES:
                time.sleep(0.5)
    return _fail(req_id, item, idx, time.perf_counter() - t0, err, False)


def new_req_id() -> str:
    return uuid.uuid4().hex[:10]
