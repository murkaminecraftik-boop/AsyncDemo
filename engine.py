"""Async demo engine - the web port of the CLI asyncio demo.

Four execution modes over a list of "media fetch" tasks (each simulated with
``await asyncio.sleep(delay)`` exactly like the CLI used ``time.sleep`` /
``await asyncio.sleep``):

* ``sync``    - sequential blocking-style execution; total time = SUM of delays.
* ``gather``  - all tasks started at once via ``asyncio.gather``; results are
                emitted together AFTER everything completes;
                total time ~= MAX of delays.
* ``stream``  - all tasks started at once, results consumed with
                ``asyncio.as_completed`` and emitted the moment they finish.
* ``timeout`` - all tasks started at once, ``asyncio.wait(timeout=...)`` then
                pending tasks are cancelled - just like ``run_timeout_demo``.

The engine is an async generator yielding event dicts. The Flask route drives
it with a private event loop and serializes each event as one NDJSON line, so
the browser sees results progressively over a single HTTP response.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

MODES = ("sync", "gather", "stream", "timeout")

MODE_TITLES = {
    "sync": "Synchronous blocking demo",
    "gather": "Asynchronous gather demo",
    "stream": "Asynchronous streaming demo",
    "timeout": "Timeout demo",
}

SaveRunFn = Callable[..., int]


def _fmt_seconds(ms: float) -> str:
    """Format milliseconds like the CLI prints (2 decimal places)."""
    return f"{ms / 1000:.2f}s"


async def engine(
    items: list[dict[str, Any]],
    mode: str,
    timeout_ms: int | None,
    save_run: SaveRunFn,
) -> Any:
    """Yield demo events; persist the run once it finishes naturally."""
    t0 = time.perf_counter()

    def now_ms() -> int:
        return round((time.perf_counter() - t0) * 1000)

    sequential_ms = sum(int(item["delay_ms"]) for item in items)
    total_tasks = len(items)
    pending_tasks: list[asyncio.Task[dict[str, Any]]] = []
    finished_tasks: list[dict[str, Any]] = []

    try:
        yield {
            "type": "run-start",
            "mode": mode,
            "title": MODE_TITLES[mode],
            "totalTasks": total_tasks,
            "sequentialMs": sequential_ms,
            "timeoutMs": timeout_ms,
            "tasks": [
                {
                    "key": item["key"],
                    "name": item["name"],
                    "kind": item["kind"],
                    "sizeMb": item["size_mb"],
                    "delayMs": int(item["delay_ms"]),
                }
                for item in items
            ],
            "t": now_ms(),
        }
        yield {"type": "log", "message": f"--- {MODE_TITLES[mode]} ---", "t": now_ms()}
        if mode == "sync":
            started_at = 0
            for item in items:
                delay_ms = int(item["delay_ms"])
                yield {
                    "type": "task-start",
                    "key": item["key"],
                    "name": item["name"],
                    "delayMs": delay_ms,
                    "startedAt": started_at,
                    "t": now_ms(),
                }
                await asyncio.sleep(delay_ms / 1000)
                finished = now_ms()
                yield {
                    "type": "task-done",
                    "key": item["key"],
                    "name": item["name"],
                    "kind": item["kind"],
                    "url": item["url"],
                    "sizeMb": item["size_mb"],
                    "delayMs": delay_ms,
                    "startedAt": started_at,
                    "t": finished,
                }
                finished_tasks.append(
                    {
                        "key": item["key"],
                        "name": item["name"],
                        "kind": item["kind"],
                        "status": "done",
                        "delay_ms": delay_ms,
                        "started_at_ms": started_at,
                        "finished_at_ms": finished,
                    }
                )
                started_at += delay_ms
        else:
            finish_ms: dict[str, int] = {}

            async def fetch(item: dict[str, Any]) -> dict[str, Any]:
                await asyncio.sleep(int(item["delay_ms"]) / 1000)
                finish_ms[item["key"]] = now_ms()
                return item

            for item in items:
                yield {
                    "type": "task-start",
                    "key": item["key"],
                    "name": item["name"],
                    "delayMs": int(item["delay_ms"]),
                    "startedAt": 0,
                    "t": now_ms(),
                }

            tasks = [asyncio.create_task(fetch(item)) for item in items]
            task_to_item: dict[asyncio.Task[dict[str, Any]], dict[str, Any]] = {
                task: item for task, item in zip(tasks, items)
            }
            pending_tasks = tasks

            if mode == "gather":
                done_items = await asyncio.gather(*tasks)
                pending_tasks = []
                for item in done_items:
                    finished = finish_ms.get(item["key"], now_ms())
                    yield {
                        "type": "task-done",
                        "key": item["key"],
                        "name": item["name"],
                        "kind": item["kind"],
                        "url": item["url"],
                        "sizeMb": item["size_mb"],
                        "delayMs": int(item["delay_ms"]),
                        "startedAt": 0,
                        "t": finished,
                    }
                    finished_tasks.append(
                        {
                            "key": item["key"],
                            "name": item["name"],
                            "kind": item["kind"],
                            "status": "done",
                            "delay_ms": int(item["delay_ms"]),
                            "started_at_ms": 0,
                            "finished_at_ms": finished,
                        }
                    )

            elif mode == "stream":
                completed_count = 0
                for coro in asyncio.as_completed(tasks):
                    item = await coro
                    completed_count += 1
                    finished = finish_ms.get(item["key"], now_ms())
                    yield {
                        "type": "task-done",
                        "key": item["key"],
                        "name": item["name"],
                        "kind": item["kind"],
                        "url": item["url"],
                        "sizeMb": item["size_mb"],
                        "delayMs": int(item["delay_ms"]),
                        "startedAt": 0,
                        "t": finished,
                    }
                    yield {
                        "type": "log",
                        "message": (
                            f"Result received {item['name']} | "
                            f"completed {completed_count} of {total_tasks} | "
                            f"clock {finished / 1000:.2f}s"
                        ),
                        "t": now_ms(),
                    }
                    finished_tasks.append(
                        {
                            "key": item["key"],
                            "name": item["name"],
                            "kind": item["kind"],
                            "status": "done",
                            "delay_ms": int(item["delay_ms"]),
                            "started_at_ms": 0,
                            "finished_at_ms": finished,
                        }
                    )
                pending_tasks = []

            elif mode == "timeout":
                limit_s = (timeout_ms or 2500) / 1000
                done_set, pending_set = await asyncio.wait(tasks, timeout=limit_s)
                pending_tasks = []

                cancelled_names: list[str] = []
                if pending_set:
                    for task in pending_set:
                        task.cancel()
                    await asyncio.gather(*pending_set, return_exceptions=True)

                done_items = [task.result() for task in done_set if not task.cancelled()]
                done_items.sort(key=lambda it: finish_ms.get(it["key"], 0))
                for item in done_items:
                    finished = finish_ms.get(item["key"], now_ms())
                    yield {
                        "type": "task-done",
                        "key": item["key"],
                        "name": item["name"],
                        "kind": item["kind"],
                        "url": item["url"],
                        "sizeMb": item["size_mb"],
                        "delayMs": int(item["delay_ms"]),
                        "startedAt": 0,
                        "t": finished,
                    }
                    finished_tasks.append(
                        {
                            "key": item["key"],
                            "name": item["name"],
                            "kind": item["kind"],
                            "status": "done",
                            "delay_ms": int(item["delay_ms"]),
                            "started_at_ms": 0,
                            "finished_at_ms": finished,
                        }
                    )

                for task in pending_set:
                    item = task_to_item[task]
                    cancelled_names.append(item["name"])
                    cancelled_at = now_ms()
                    yield {
                        "type": "task-cancel",
                        "key": item["key"],
                        "name": item["name"],
                        "delayMs": int(item["delay_ms"]),
                        "t": cancelled_at,
                    }
                    finished_tasks.append(
                        {
                            "key": item["key"],
                            "name": item["name"],
                            "kind": item["kind"],
                            "status": "cancelled",
                            "delay_ms": int(item["delay_ms"]),
                            "started_at_ms": 0,
                            "finished_at_ms": cancelled_at,
                        }
                    )

                yield {
                    "type": "log",
                    "message": (
                        f"Timeout limit {limit_s:.1f}s | completed before timeout "
                        f"{len(done_items)} of {total_tasks} | cancelled {len(cancelled_names)}"
                    ),
                    "t": now_ms(),
                }
        elapsed_ms = now_ms()
        completed = len(finished_tasks) - sum(
            1 for t in finished_tasks if t["status"] == "cancelled"
        )
        cancelled = sum(1 for t in finished_tasks if t["status"] == "cancelled")
        throughput = round(completed / (elapsed_ms / 1000), 2) if elapsed_ms > 0 else 0.0
        saved_ms = sequential_ms - elapsed_ms

        run_id = save_run(
            mode=mode,
            completed=completed,
            cancelled=cancelled,
            elapsed_ms=elapsed_ms,
            sequential_ms=sequential_ms,
            timeout_ms=timeout_ms if mode == "timeout" else None,
            throughput=throughput,
            tasks=finished_tasks,
        )

        yield {
            "type": "metrics",
            "mode": mode,
            "elapsedMs": elapsed_ms,
            "completed": completed,
            "cancelled": cancelled,
            "totalTasks": total_tasks,
            "sequentialMs": sequential_ms,
            "savedMs": saved_ms,
            "throughput": throughput,
            "runId": run_id,
            "t": now_ms(),
        }
        yield {"type": "end", "t": now_ms()}

    finally:
        if pending_tasks:
            for task in pending_tasks:
                task.cancel()
            try:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            except Exception:
                pass


def make_sync_stream(
    items: list[dict[str, Any]],
    mode: str,
    timeout_ms: int | None,
    save_run: SaveRunFn,
) -> Callable[..., Any]:
    """Return a sync generator that drives the async engine on its own loop.

    Flask is WSGI, so the response body must be a regular (sync) iterator.
    Each ``run_until_complete`` call advances the async generator exactly one
    event, which is then serialized by the route as one NDJSON line.
    """

    def generate() -> Any:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        agen = engine(items, mode, timeout_ms, save_run)
        try:
            while True:
                try:
                    event = loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
                yield (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
        finally:
            try:
                loop.run_until_complete(agen.aclose())
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass

    return generate