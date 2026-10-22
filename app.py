from __future__ import annotations

import os
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import db as database
from engine import MODES, make_sync_stream

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

MIN_TIMEOUT_MS = 500
MAX_TIMEOUT_MS = 8000
MAX_TASKS = 16


@app.get("/")
def index() -> str:
    items = database.list_media()
    return render_template("index.html", items=items)


@app.get("/api/media")
def api_media() -> Response:
    return jsonify({"items": database.list_media()})


@app.post("/api/run")
def api_run() -> Response:
    payload: dict[str, Any] = request.get_json(silent=True) or {}

    mode = payload.get("mode")
    if mode not in MODES:
        return jsonify({"error": f"mode must be one of: {', '.join(MODES)}"}), 400

    keys = payload.get("media")
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
        return jsonify({"error": "media must be a non-empty list of catalog keys"}), 400
    if len(keys) > MAX_TASKS:
        return jsonify({"error": f"too many tasks (max {MAX_TASKS})"}), 400

    items = database.get_media_by_keys(keys)
    if len(items) != len(set(keys)):
        return jsonify({"error": "unknown media key in request"}), 400

    timeout_ms: int | None = None
    if mode == "timeout":
        raw = payload.get("timeoutMs", 2500)
        if not isinstance(raw, (int, float)) or not (MIN_TIMEOUT_MS <= raw <= MAX_TIMEOUT_MS):
            return jsonify(
                {"error": f"timeoutMs must be between {MIN_TIMEOUT_MS} and {MAX_TIMEOUT_MS}"}
            ), 400
        timeout_ms = int(raw)

    generator = make_sync_stream(
        items, mode, timeout_ms, save_run=database.save_run
    )

    return Response(
        stream_with_context(generator()),
        mimetype="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
def api_history() -> Response:
    return jsonify({"runs": database.list_runs(limit=15)})


@app.get("/api/health")
def api_health() -> Response:
    return jsonify({"status": "ok"})


@app.errorhandler(404)
def not_found(_error: Any) -> Response:
    return jsonify({"error": "not found"}), 404


def main() -> None:
    database.init_db()
    port = int(os.environ.get("PORT", "3000"))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
