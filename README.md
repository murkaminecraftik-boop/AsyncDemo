# Async Media Fetcher — Flask + asyncio

Асинхронный веб-сайт: пользователь просит гифки или тяжёлые видео — сайт
параллельно получает их (asyncio + httpx) и отправляет пользователю потоком
(Server-Sent Events) с живыми метриками. Это веб-версия CLI-демо асинхронности:
все режимы (`sync` / `gather` / `as_completed` / `timeout` / сравнение) перенесены
из консоли в браузер.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
# откройте http://127.0.0.1:5000
```

Переменные окружения:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `PORT` | `5000` | порт сервера |
| `FORCE_SIM` | — | `FORCE_SIM=1` — принудительный демо-режим без обращений в сеть |

## Два режима работы

- **real** (по умолчанию): сайт реально скачивает файлы из сети — GIF с Giphy CDN,
  видео с test-videos.co.uk (CC) и MDN (CC0). Байты складываются во временное
  хранилище и раздаются браузеру через `/media/<req_id>/<item_id>`.
- **sim** (включается автоматически, если сеть недоступна, или через `FORCE_SIM=1`):
  «загрузка» имитируется `asyncio.sleep` с плановыми задержками (аналог `delay`
  из CLI-примера), а вместо файлов отдаются локальные заглушки — анимированные
  GIF (Pillow) и mp4 (ffmpeg `testsrc2`). Идеален для демонстрации таймаута:
  «тяжёлое видео» имеет плановую задержку 6.5 с и гарантированно отменяется
  при таймауте 3–5 с.

Режим определяется при старте (проверка доступности сети), бейдж в шапке
страницы показывает текущий режим. Если в real-режиме сеть недоступна
(все задачи упали), `/api/fetch` автоматически повторяет запрос в demo-режиме.

## Режимы в интерфейсе

| Режим | Что делает | Эндпоинт |
|---|---|---|
| **Поток (as_completed)** | Карточки появляются по мере готовности каждой задачи — SSE-стрим событий | `GET /api/stream?mode=stream` |
| **gather** | Ждём все задачи, один ответ + метрики | `GET /api/fetch` |
| **Таймаут** | `asyncio.wait(timeout=…)` + отмена зависших задач | `GET /api/stream?mode=timeout` |
| **Sync (блокирующий)** | Последовательные блокирующие загрузки — базовая линия | `GET /api/stream?mode=sync` |
| **Sync vs Async** | Один и тот же набор качается дважды, таблица сравнения | `GET /api/compare` |

Настройки: тип медиа (гифки / видео / микс), количество (2–8), степень
параллелизма (`asyncio.Semaphore`, 1–8), таймаут (1–15 с).

## Соответствие CLI-коду

| CLI-демо | Здесь |
|---|---|
| `run_sync_demo` / `time.sleep` | Режим «Sync» → `/api/stream?mode=sync` |
| `run_async_gather_demo` / `asyncio.gather` | Режим «gather» → `/api/fetch` |
| `run_async_streaming_demo` / `asyncio.as_completed` | Режим «Поток» → `/api/stream?mode=stream` (SSE) |
| `run_timeout_demo` / `asyncio.wait` + cancel | Режим «Таймаут» → `/api/stream?mode=timeout` |
| `print_comparison` | Режим «Sync vs Async» → `/api/compare` |
| `print_metrics` / `DemoResult` | Панель «Метрики» (mode, completed, elapsed, sequential estimate, time saved, throughput, bytes) |
| Логи `Start …` / `Done …` / `Result received …` | Панель «Консоль» (те же строки, событие за событием) |

## API

- `GET /` — страница.
- `GET /api/health` — `{"ok": true, "mode": "real|sim", "ffmpeg": bool}`.
- `GET /api/library` — библиотека источников + режим.
- `GET /api/fetch?kind=gif|video|mix&count=N&concurrency=M` — режим gather,
  JSON: `{req_id, items[], metrics, fallback_simulated}`.
- `GET /api/stream?mode=stream|sync|timeout&kind=…&count=…&timeout=T` — SSE,
  события: `start`, `log`, `item`, `cancelled`, `fail`, `timeout_info`, `metrics`.
- `GET /api/compare?kind=…&count=…` — sync vs async, JSON: `{sync, async}`.
- `GET /media/<req_id>/<item_id>[?download=1]` — отдача скачанных байтов
  (в памяти, TTL 20 мин, лимит 250 МБ).

Пример:

```bash
curl -N "http://127.0.0.1:5000/api/stream?kind=video&count=4&mode=timeout&timeout=3"
```

## Как устроен код

```
flask-async-media/
├── app.py               # Flask-роуты: страницы, /api/*, SSE-генератор, /media
├── media.py             # библиотека источников, async/sync загрузка, метрики, заглушки
├── templates/index.html # UI ( controls, консоль, карточки, метрики)
├── static/css/style.css # тёмная тема
├── static/js/app.js     # EventSource, карточки, таблицы метрик
└── requirements.txt
```

Ключевые приёмы:

- Асинхронные view Flask (`async def` + `flask[async]`, под капотом asgiref).
- Для SSE — синхронный генератор ответа, внутри которого на каждый запрос
  создаётся свой event loop; задачи гоняются через `asyncio.as_completed`
  или `asyncio.wait`, а готовые результаты уходят клиенту через `asyncio.Queue`.
- `asyncio.Semaphore` ограничивает параллелизм; тайминги считаются после входа
  в семафор, чтобы «последовательная оценка» была честной.
- Скачанные байты кэшируются в памяти и раздаются отдельным эндпоинтом.

## Ограничения и прод-советы

- Это демо: скачанные файлы хранятся в памяти процесса, dev-сервер Werkzeug
  однопоточен на запрос. Для продакшена — gunicorn с воркерами/тредами,
  вынос хранилища в Redis/S3, а для настоящего push — ASGI (FastAPI/Starlette).
- В режиме Sync vs Async файлы скачиваются дважды (sync-проход + async-проход),
  поэтому сравнение ограничено 4 элементами.
