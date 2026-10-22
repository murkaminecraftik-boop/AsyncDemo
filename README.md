# ⚡ Async Media Lab

A full-stack demo of **Python asyncio on the web**: request GIFs and heavy videos,
and the Flask backend "fetches" them concurrently (or sequentially — your choice)
while streaming every event to the browser over a single HTTP response.

It is the direct web port of the classic CLI asyncio demo
(`time.sleep` vs `asyncio.gather` vs `asyncio.as_completed` vs `asyncio.wait(timeout=...)`):

| CLI demo | Web demo | Behaviour |
| --- | --- | --- |
| `run_sync_demo` | **Sequential** | one request at a time; total ≈ **sum** of all waits |
| `run_async_gather_demo` | **Gather** | all at once via `asyncio.gather`; results rendered when the last finishes; total ≈ **max** wait |
| `run_async_streaming_demo` | **Streaming** | `asyncio.as_completed`; each result is streamed to the page the moment it is done |
| `run_timeout_demo` | **Timeout** | `asyncio.wait(timeout=...)`; everything slower than the limit is cancelled |

## Architecture

```
flask_app/
├── app.py              # Flask routes: / , /api/media , /api/run (NDJSON stream), /api/history
├── engine.py           # asyncio engine: async generator per mode, wrapped in a sync generator for WSGI
├── db.py               # SQLite layer: media catalog + run history (schema, seed, queries)
├── requirements.txt    # flask
├── run.sh              # venv bootstrap + start
├── templates/
│   └── index.html      # single-page UI (Jinja2)
└── static/
    ├── css/style.css   # dark "observability" theme, responsive, no frameworks
    └── js/app.js       # NDJSON stream reader, live console, gallery, metrics, history
└── data/
    └── media_lab.db    # SQLite (created automatically)
```

### How the async part works

Flask is WSGI, so a response body must be a *sync* iterator.
`engine.make_sync_stream()` bridges the two worlds: it creates a private
event loop per request and advances the async generator one event at a time
with `loop.run_until_complete(agen.__anext__())`. Each event is serialized as
one NDJSON line (`application/x-ndjson`), so the browser's `fetch` + `ReadableStream`
reader renders results progressively — the web equivalent of the CLI printing
`Start ... / Done ...` lines while tasks complete.

The simulated "heavy fetch" is `await asyncio.sleep(delay_ms / 1000)` per media
item, proportional to the real file size (videos are slow, GIFs are fast), with
the real files delivered afterwards from their public CDNs.

### Database

SQLite (stdlib `sqlite3`) with three tables:

- `media_items` — the catalog: 6 animated GIFs + 5 heavy videos (real URLs, real sizes)
- `runs` — one row per completed demo run (mode, timings, throughput)
- `run_tasks` — one row per task with per-task start/finish timestamps

## Running

```bash
cd flask_app
./run.sh            # creates venv on first run, starts Flask on 0.0.0.0:3000
```

or manually:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py
```

Open `http://localhost:3000`.

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | single-page UI |
| GET | `/api/media` | media catalog |
| POST | `/api/run` | body: `{"mode": "sync|gather|stream|timeout", "media": ["typing-cat", ...], "timeoutMs": 2500}` → NDJSON event stream |
| GET | `/api/history` | last 15 runs from SQLite |
| GET | `/api/health` | liveness probe |

### Event stream protocol (NDJSON, one JSON object per line)

```
{"type": "run-start",  "mode": "gather", "totalTasks": 6, "sequentialMs": 8200, "tasks": [...], "t": 0}
{"type": "task-start", "key": "typing-cat", "name": "Typing Cat", "delayMs": 1300, "t": 1}
{"type": "task-done",  "key": "typing-cat", "url": "...", "startedAt": 0, "t": 1302}
{"type": "task-cancel","key": "bbb-full", "name": "Big Buck Bunny: Full Movie", "t": 2501}
{"type": "metrics",    "elapsedMs": 3003, "completed": 6, "savedMs": 5197, "throughput": 2.0, "runId": 5}
{"type": "end",        "t": 3004}
```

Cancelling from the client (Stop button) aborts the fetch; the server-side
generator's `finally` block cancels all pending asyncio tasks, so nothing leaks.

## What to try

1. Select all **GIFs** → **Sequential** → run: watch them appear strictly one by one (~8.2s).
2. Same selection → **Gather** → run: everything lands at once after ~3.0s (the max delay).
3. Same selection → **Streaming** → run: results pop in as each task finishes.
4. Select the **heavy videos**, set the timeout slider to ~1.5s → **Timeout** → run:
   only the fast ones survive, the rest are cancelled at the limit.
5. Compare the metrics cards, the session comparison table and the SQLite history below.
