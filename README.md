# async-media-fetcher

A small Flask site that demonstrates asyncio the same way the original
command-line demo did: you ask for GIFs or heavyweight videos, the server
downloads them concurrently and streams every result back to the page the
moment it lands. Same run modes, same metrics (`DemoResult` fields), same
console lines — just in a browser instead of a terminal.

The frontend is intentionally plain: one HTML file, one CSS file, one JS
file, native form controls, no framework and no build step. It should look
like something a backend developer put together over a weekend — because
it is.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (source .venv/bin/activate on Linux/macOS)
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

> **Note on the old `RuntimeError: Install Flask with the 'async' extra` crash:**
> the app no longer uses Flask async views at all, so it runs on a plain
> `pip install flask`. All asyncio work happens inside `asyncio.run()` calls
> and a private event loop in the SSE generator.

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `5000` | server port |
| `FORCE_SIM` | — | `FORCE_SIM=1` forces demo mode (no outbound requests) |

## What's new in v0.3.1

- **Cheaper video playback.** Browsers buffer a large `<video>` as a burst
  of `Range` requests (a 30 MB file easily produces a dozen `206`s). `/media/…`
  now issues a strong `ETag` and honors `If-None-Match` (→ `304`) and
  `If-Range` (matching → `206` slice, stale → full `200`), so the browser
  never re-transfers bytes it already has.
- **One media element per row, ever.** The client now marks a row complete
  exactly once (`data-done`), so a repeated SSE event can no longer re-append
  a `<video>` and make the browser restart the download.
- **UI pass.** Same plain layout, warmer and more specific: paper background
  with a faint dot grid, rust accent, mono section numbers (`01 request`),
  dotted table rules, a blinking block caret after the title, diagonal
  stripes crawling through in-flight progress bars, amber-on-dark console.

## What was new in v0.3

- **UI rework** — dropped the dark neon theme for a plain light developer
  look: system fonts, one accent colour, native radios/selects, results as
  rows with per-item progress bars instead of picture cards, real tables
  for metrics, mono status line, a console with sticky autoscroll.
- UI copy is now in English, media titles are the real asset ids / file
  names (no invented "Aurora"-style labels).
- Live status line: `running · 2/4 done · 11.3 MB · 4.2 s`.
- Timeout hits are reported as an inline note, not a toast popup.
- Keyboard: `Ctrl+Enter` starts, `Esc` stops.

Carried over from v0.2: SSE `progress` events (byte-level, throttled at
0.35 s), Range/206 support in `/media/…` so `<video>` can seek, 2 retries
per download, content sniffing by magic bytes, auto-fallback to sim mode
when the network is dead.

## Two run modes (server side)

- **real** (default): the site actually downloads files — GIFs from Giphy
  CDN, videos from test-videos.co.uk (CC) and MDN (CC0). Bytes are kept in
  an in-memory store and served from `/media/<req_id>/<item_id>`.
- **sim**: enabled automatically when the network is unreachable, or forced
  with `FORCE_SIM=1`. Downloads are simulated with `asyncio.sleep` (the
  "planned wait" plays the role of the fixed `delay` from the CLI demo) and
  the payloads are local fixtures: animated GIFs via Pillow, mp4 via ffmpeg
  `testsrc2`. The heavy video (30 MB / 6.5 s planned) exists so the timeout
  mode has something to cancel.

The mode is detected at startup; the badge in the header shows it. If every
download fails in real mode, `/api/fetch` retries the whole batch in sim
mode so the demo never dead-ends.

## Run modes in the UI

| Mode | What it does | Endpoint |
|---|---|---|
| **stream** | results stream in over SSE as each task finishes | `GET /api/stream?mode=stream` |
| **gather** | waits for the whole batch, one JSON response | `GET /api/fetch` |
| **timeout** | `asyncio.wait(timeout=…)` + cancels stragglers | `GET /api/stream?mode=timeout` |
| **sync** | baseline: blocking downloads one by one | `GET /api/stream?mode=sync` |
| **compare** | same batch twice (blocking + concurrent), side-by-side table | `GET /api/compare` |

Settings: media type (GIFs / videos / mix), batch size (2–8), concurrency
(`asyncio.Semaphore`, 1–8), timeout (1–15 s).

## Mapping to the CLI demo

| CLI demo | This app |
|---|---|
| `run_sync_demo` / `time.sleep` | mode `sync` → `/api/stream?mode=sync` |
| `run_async_gather_demo` / `asyncio.gather` | mode `gather` → `/api/fetch` |
| `run_async_streaming_demo` / `asyncio.as_completed` | mode `stream` → `/api/stream?mode=stream` (SSE) |
| `run_timeout_demo` / `asyncio.wait` + cancel | mode `timeout` → `/api/stream?mode=timeout` |
| `print_comparison` | mode `compare` → `/api/compare` |
| `print_metrics` / `DemoResult` | Metrics panel (mode, completed, elapsed, sequential estimate, time saved, throughput, bytes) |
| `Start …` / `Done …` / `Result received …` logs | Console panel, same lines, event by event |

## API

- `GET /` — the page.
- `GET /api/health` — `{"ok": true, "mode": "real|sim", "ffmpeg": bool}`.
- `GET /api/library` — source library + current mode.
- `GET /api/fetch?kind=gif|video|mix&count=N&concurrency=M` — gather mode,
  JSON: `{req_id, items[], metrics, fallback_simulated}`.
- `GET /api/stream?mode=stream|sync|timeout&kind=…&count=…&timeout=T` — SSE,
  events: `start`, `log`, `progress`, `item`, `cancelled`, `fail`,
  `timeout_info`, `metrics`.
- `GET /api/compare?kind=…&count=…` — sync vs async, JSON: `{sync, async}`.
- `GET /media/<req_id>/<item_id>[?download=1]` — serves downloaded bytes
  (in memory, TTL 20 min, 250 MB cap, Range supported).

Example:

```bash
curl -N "http://127.0.0.1:5000/api/stream?kind=video&count=4&mode=timeout&timeout=3"
```

## Project layout

```
flask-async-media/
├── app.py                # Flask routes: page, /api/*, SSE generator, /media
├── media.py              # source library, async/sync downloads, metrics, sim fixtures
├── templates/index.html  # markup (no framework, no build step)
├── static/css/style.css  # light theme, one accent colour
├── static/js/app.js      # EventSource, rows, tables — plain JS
└── requirements.txt      # flask, httpx, requests, pillow — no asgiref needed
```

How the async parts work:

- Flask serves everything synchronously; no `async def` views anywhere.
- `/api/fetch` and `/api/compare` run their batch through `asyncio.run()`.
- `/api/stream` is a sync generator that creates an event loop per request,
  drives tasks with `asyncio.as_completed` / `asyncio.wait`, and forwards
  results through an `asyncio.Queue` into SSE events.
- `asyncio.Semaphore` caps concurrency; per-task timings start after the
  semaphore is acquired so the "sequential estimate" stays honest.
- Downloaded bytes are cached in memory and served by a separate endpoint.

## Limitations / production notes

- This is a demo: downloaded files live in the process memory and Werkzeug's
  dev server serves one request at a time per thread. For production use
  gunicorn with multiple workers, move the byte store to Redis/S3, and if
  you need real server push, move to ASGI (FastAPI/Starlette).
- In compare mode the same files are downloaded twice (blocking pass +
  concurrent pass), which is why it is capped at 4 items.
