"""
app.py — асинхронный сайт на Flask: «попроси гифки или тяжёлые видео — получи их».

Пользователь (браузер) просит медиа, сайт параллельно (asyncio + httpx) получает
их и отправляет пользователю: потоком через Server-Sent Events или одним JSON.

Соответствие CLI-демо:
  run_sync_demo / time.sleep            -> GET /api/stream?mode=sync
  run_async_gather_demo / gather        -> GET /api/fetch
  run_async_streaming_demo / as_completed -> GET /api/stream?mode=stream (SSE)
  run_timeout_demo / asyncio.wait       -> GET /api/stream?mode=timeout (SSE)
  print_comparison                      -> GET /api/compare
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
from flask import Flask, Response, abort, jsonify, render_template, request

import media

app = Flask(__name__)
app.json.ensure_ascii = False

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

DEMO_TITLES = {
    "stream": "Asynchronous streaming demo (as_completed)",
    "sync": "Synchronous blocking demo (по очереди)",
    "timeout": "Timeout demo (asyncio.wait + cancel)",
}


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def get_params():
    kind = request.args.get("kind", "gif")
    if kind not in ("gif", "video", "mix"):
        kind = "gif"
    try:
        count = int(request.args.get("count", 4))
    except ValueError:
        count = 4
    count = max(1, min(count, 8))
    try:
        conc = int(request.args.get("concurrency", 4))
    except ValueError:
        conc = 4
    conc = max(1, min(conc, 8))
    try:
        timeout = float(request.args.get("timeout", 0) or 0)
    except ValueError:
        timeout = 0.0
    return kind, count, conc, timeout


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def pack(results, metrics, req_id: str) -> dict:
    return {
        "req_id": req_id,
        "items": [r.as_dict() for r in results],
        "metrics": metrics.as_dict(),
    }


# --------------------------------------------------------------------------
# Страницы и служебные эндпоинты
# --------------------------------------------------------------------------

@app.get("/")
def index():
    media.detect_mode()
    return render_template("index.html", mode=media.detect_mode())


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "mode": media.detect_mode(),
                    "ffmpeg": bool(media.FFMPEG)})


@app.get("/api/library")
def library():
    return jsonify({
        "mode": media.detect_mode(),
        "items": [{
            "id": it.id, "kind": it.kind, "title": it.title,
            "size_hint_mb": it.size_hint_mb, "sim_delay": it.sim_delay,
        } for it in media.LIBRARY],
    })


# --------------------------------------------------------------------------
# Режим gather: asyncio.gather — ответ одним JSON, когда всё готово
# --------------------------------------------------------------------------

@app.route("/api/fetch", methods=["GET", "POST"])
async def api_fetch():
    kind, count, conc, _ = get_params()
    items = media.select_items(kind, count)
    simulated = media.is_simulated()

    async def run(sim: bool, req: str):
        sem = asyncio.Semaphore(conc)
        async with httpx.AsyncClient(headers=media.headers(),
                                     timeout=media.HTTP_TIMEOUT) as client:
            return await asyncio.gather(
                *(media.fetch_item(client, it, req, sem, sim, idx=i)
                  for i, it in enumerate(items))
            )

    req_id = media.new_req_id()
    t0 = time.perf_counter()
    results = await run(simulated, req_id)
    elapsed = time.perf_counter() - t0

    # Мягкий фолбэк: если реальная сеть недоступна (все задачи упали),
    # повторяем в демо-режиме, чтобы демо всегда работало.
    fallback = False
    if not simulated and results and all(not r.ok for r in results):
        fallback, simulated = True, True
        req_id = media.new_req_id()
        t0 = time.perf_counter()
        results = await run(True, req_id)
        elapsed = time.perf_counter() - t0

    metrics = media.compute_metrics("async gather", list(results), len(items), elapsed)
    resp = pack(list(results), metrics, req_id)
    resp["fallback_simulated"] = fallback
    return jsonify(resp)


# --------------------------------------------------------------------------
# SSE-стрим: события на каждое завершение задачи (stream / sync / timeout)
# --------------------------------------------------------------------------

def _planned_label(item: media.MediaItem, simulated: bool) -> str:
    return (f"planned wait {item.sim_delay:.1f}s" if simulated
            else f"~{item.size_hint_mb:.1f} MB")


@app.get("/api/stream")
def api_stream():
    kind, count, conc, timeout = get_params()
    mode = request.args.get("mode", "stream")
    if mode not in ("stream", "sync", "timeout"):
        mode = "stream"
    if mode == "timeout" and timeout <= 0:
        timeout = 5.0
    items = media.select_items(kind, count)
    simulated = media.is_simulated()

    def generate():
        total = len(items)
        req_id = media.new_req_id()
        yield sse("start", {
            "req_id": req_id, "mode": mode, "simulated": simulated,
            "total": total, "title": DEMO_TITLES[mode],
            "items": [{
                "index": i, "item_id": it.id, "title": it.title,
                "kind": it.kind, "size_hint_mb": it.size_hint_mb,
                "sim_delay": it.sim_delay,
            } for i, it in enumerate(items)],
        })
        yield sse("log", {"msg": f"--- {DEMO_TITLES[mode]} ---"})

        # ---------- sync: блокирующие загрузки по очереди ----------
        if mode == "sync":
            t0 = time.perf_counter()
            results = []
            for i, it in enumerate(items):
                yield sse("log", {"msg": f"Start {it.title} | {_planned_label(it, simulated)}"})
                r = media.fetch_item_sync(it, req_id, simulated, idx=i)
                results.append(r)
                clock = time.perf_counter() - t0
                yield sse("item", r.as_dict())
                done_msg = (f"Done {it.title} | planned wait {it.sim_delay:.1f}s | "
                            f"actual wait {r.elapsed:.2f}s" if simulated else
                            f"Done {it.title} | {r.size_bytes / 1e6:.1f} MB | "
                            f"actual wait {r.elapsed:.2f}s")
                yield sse("log", {"msg": done_msg})
                yield sse("log", {"msg": (f"Result received {it.title} | "
                                          f"completed {i + 1} of {total} | clock {clock:.2f}s")})
            elapsed = time.perf_counter() - t0
            yield sse("metrics", media.compute_metrics(
                "sync sequential", results, total, elapsed).as_dict())
            return

        # ---------- асинхронные режимы: свой event loop на этот запрос ----------
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        q: asyncio.Queue = asyncio.Queue()

        async def orchestrate():
            started = time.perf_counter()
            mode_name = {"stream": "async streaming", "timeout": "async timeout"}[mode]
            results: list[media.FetchResult] = []
            cancelled = 0
            async with httpx.AsyncClient(headers=media.headers(),
                                         timeout=media.HTTP_TIMEOUT) as client:
                sem = asyncio.Semaphore(conc)
                tasks, tmap = [], {}
                for i, it in enumerate(items):
                    await q.put(("log", {"msg": f"Start {it.title} | {_planned_label(it, simulated)}"}))
                    t = loop.create_task(
                        media.fetch_item(client, it, req_id, sem, simulated, idx=i))
                    tasks.append(t)
                    tmap[id(t)] = (it, i)

                if mode == "timeout":
                    done, pending = await asyncio.wait(tasks, timeout=timeout)
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    for t in done:
                        it, i = tmap[id(t)]
                        if t.cancelled():
                            continue
                        exc = t.exception()
                        if exc is not None:
                            await q.put(("fail", {"item_id": it.id, "index": i,
                                                  "title": it.title, "message": str(exc)}))
                            continue
                        r = t.result()
                        results.append(r)
                        await q.put(("item", r.as_dict()))
                    for t in pending:
                        it, i = tmap[id(t)]
                        cancelled += 1
                        await q.put(("cancelled", {"item_id": it.id, "index": i,
                                                   "title": it.title}))
                    await q.put(("timeout_info", {
                        "timeout": timeout, "completed": len(results),
                        "cancelled": cancelled,
                    }))
                else:
                    done_n = 0
                    for fut in asyncio.as_completed(tasks):
                        r = await fut
                        results.append(r)
                        done_n += 1
                        clock = time.perf_counter() - started
                        await q.put(("item", r.as_dict()))
                        await q.put(("log", {"msg": (
                            f"Result received {r.title} | "
                            f"completed {done_n} of {len(tasks)} | clock {clock:.2f}s")}))

            elapsed = time.perf_counter() - started
            await q.put(("metrics", media.compute_metrics(
                mode_name, results, total, elapsed, cancelled=cancelled).as_dict()))
            await q.put(None)

        runner = loop.create_task(orchestrate())
        try:
            while True:
                ev = loop.run_until_complete(q.get())
                if ev is None:
                    break
                yield sse(*ev)
            loop.run_until_complete(runner)
        finally:
            if not runner.done():
                runner.cancel()
                try:
                    loop.run_until_complete(runner)
                except BaseException:
                    pass
            loop.close()
            asyncio.set_event_loop(None)

    return Response(generate(), mimetype="text/event-stream", headers=SSE_HEADERS)


# --------------------------------------------------------------------------
# Сравнение sync vs async на одних и тех же файлах
# --------------------------------------------------------------------------

@app.get("/api/compare")
def api_compare():
    kind, count, conc, _ = get_params()
    count = min(count, 4)  # sync-часть качает всё по очереди — не мучаем пользователя
    items = media.select_items(kind, count)
    simulated = media.is_simulated()

    async def run_async():
        req = media.new_req_id()
        sem = asyncio.Semaphore(conc)
        async with httpx.AsyncClient(headers=media.headers(),
                                     timeout=media.HTTP_TIMEOUT) as client:
            return req, await asyncio.gather(
                *(media.fetch_item(client, it, req, sem, simulated, idx=i)
                  for i, it in enumerate(items))
            )

    t0 = time.perf_counter()
    req_id_a, results_a = asyncio.run(run_async())
    elapsed_a = time.perf_counter() - t0
    metrics_a = media.compute_metrics("async gather", list(results_a), len(items), elapsed_a)

    req_id_s = media.new_req_id()
    t0 = time.perf_counter()
    results_s = [media.fetch_item_sync(it, req_id_s, simulated, idx=i)
                 for i, it in enumerate(items)]
    elapsed_s = time.perf_counter() - t0
    metrics_s = media.compute_metrics("sync sequential", results_s, len(items), elapsed_s)

    return jsonify({
        "simulated": simulated,
        "async": pack(list(results_a), metrics_a, req_id_a),
        "sync": pack(list(results_s), metrics_s, req_id_s),
    })


# --------------------------------------------------------------------------
# Раздача скачанного медиа пользователю
# --------------------------------------------------------------------------

@app.get("/media/<req_id>/<item_id>")
def media_file(req_id: str, item_id: str):
    entry = media.store_get(req_id, item_id)
    if entry is None:
        abort(404)
    resp = Response(entry["bytes"],
                    content_type=entry["content_type"] or "application/octet-stream")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="{entry["filename"]}"'
    resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    media.detect_mode()
    media.warm_sim_assets()
    print(f"* Async Media demo: http://127.0.0.1:{port} | режим: {media.detect_mode()}")
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)
