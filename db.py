"""SQLite data layer for the Async Media Lab.

Two responsibilities:
1. Media catalog  - the "remote services" (GIFs / heavy videos) the demo fetches.
2. Run history    - every completed demo run with per-task timings.

Uses the standard library sqlite3 module only, so the project has no
database dependencies beyond Python itself.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "media_lab.db")

SEED_MEDIA: list[dict[str, Any]] = [
    {"key": "standing-cat", "name": "Standing Cat", "kind": "gif",
     "url": "https://media.giphy.com/media/WXB88TeARFVvi/giphy.gif",
     "size_mb": 0.15, "delay_ms": 500, "sort_order": 1},
    {"key": "skeptical-cat", "name": "Skeptical Cat", "kind": "gif",
     "url": "https://media.giphy.com/media/EDyyIDeYbHZQW19XTB/giphy.gif",
     "size_mb": 0.5, "delay_ms": 700, "sort_order": 2},
    {"key": "driving-cat", "name": "Driving Cat", "kind": "gif",
     "url": "https://media.giphy.com/media/lVCe4OgJuLzr4VWc4U/giphy.gif",
     "size_mb": 0.53, "delay_ms": 800, "sort_order": 3},
    {"key": "typing-cat", "name": "Typing Cat", "kind": "gif",
     "url": "https://media.giphy.com/media/JIX9t2j0ZTN9S/giphy.gif",
     "size_mb": 1.7, "delay_ms": 1300, "sort_order": 4},
    {"key": "grinning-cat", "name": "Grinning Cat", "kind": "gif",
     "url": "https://media.giphy.com/media/8oRH6NS8anmyoNf0qi/giphy.gif",
     "size_mb": 2.7, "delay_ms": 1900, "sort_order": 5},
    {"key": "kung-paw-cat", "name": "Kung Paw Cat", "kind": "gif",
     "url": "https://media.giphy.com/media/g2JO6qWbQhiV7m4AKM/giphy.gif",
     "size_mb": 5.3, "delay_ms": 3000, "sort_order": 6},
    {"key": "sintel-trailer", "name": "Sintel: Trailer", "kind": "video",
     "url": "https://media.w3.org/2010/05/sintel/trailer.mp4",
     "size_mb": 4.0, "delay_ms": 1400, "sort_order": 7},
    {"key": "bbb-trailer", "name": "Big Buck Bunny: Trailer", "kind": "video",
     "url": "https://media.w3.org/2010/05/bunny/trailer.mp4",
     "size_mb": 10.4, "delay_ms": 2200, "sort_order": 8},
    {"key": "oceans", "name": "Oceans", "kind": "video",
     "url": "https://vjs.zencdn.net/v/oceans.mp4",
     "size_mb": 21.0, "delay_ms": 3400, "sort_order": 9},
    {"key": "jellyfish-1080", "name": "Jellyfish 1080p", "kind": "video",
     "url": "https://test-videos.co.uk/vids/jellyfish/mp4/h264/1080/Jellyfish_1080_10s_30MB.mp4",
     "size_mb": 30.0, "delay_ms": 4200, "sort_order": 10},
    {"key": "bbb-full", "name": "Big Buck Bunny: Full Movie", "kind": "video",
     "url": "https://media.w3.org/2010/05/bunny/movie.mp4",
     "size_mb": 237.0, "delay_ms": 6000, "sort_order": 11},
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_items (
    key        TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('gif', 'video')),
    url        TEXT NOT NULL,
    size_mb    REAL NOT NULL,
    delay_ms   INTEGER NOT NULL,
    sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mode          TEXT NOT NULL,
    total_tasks   INTEGER NOT NULL,
    completed     INTEGER NOT NULL,
    cancelled     INTEGER NOT NULL,
    elapsed_ms    INTEGER NOT NULL,
    sequential_ms INTEGER NOT NULL,
    timeout_ms    INTEGER,
    throughput    REAL NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS run_tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    media_key      TEXT NOT NULL,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('done', 'cancelled')),
    delay_ms       INTEGER NOT NULL,
    started_at_ms  INTEGER NOT NULL,
    finished_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_tasks_run_id ON run_tasks (run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at DESC);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Open a connection with row access by column name and FK enforced."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create schema and seed the media catalog (idempotent)."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for item in SEED_MEDIA:
            conn.execute(
                """
                INSERT INTO media_items (key, name, kind, url, size_mb, delay_ms, sort_order)
                VALUES (:key, :name, :kind, :url, :size_mb, :delay_ms, :sort_order)
                ON CONFLICT (key) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    url = excluded.url,
                    size_mb = excluded.size_mb,
                    delay_ms = excluded.delay_ms,
                    sort_order = excluded.sort_order
                """,
                item,
            )


def list_media() -> list[dict[str, Any]]:
    """Full catalog ordered for display."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, name, kind, url, size_mb, delay_ms FROM media_items ORDER BY sort_order"
        ).fetchall()
    return [dict(row) for row in rows]


def get_media_by_keys(keys: list[str]) -> list[dict[str, Any]]:
    """Fetch catalog rows for the requested keys, preserving the given order."""
    if not keys:
        return []
    placeholders = ",".join("?" for _ in keys)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT key, name, kind, url, size_mb, delay_ms FROM media_items "
            f"WHERE key IN ({placeholders})",
            keys,
        ).fetchall()
    by_key = {row["key"]: dict(row) for row in rows}
    return [by_key[k] for k in keys if k in by_key]


def save_run(
    mode: str,
    completed: int,
    cancelled: int,
    elapsed_ms: int,
    sequential_ms: int,
    timeout_ms: int | None,
    throughput: float,
    tasks: list[dict[str, Any]],
) -> int:
    """Persist a finished run (header + one row per task). Returns the run id."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO runs (mode, total_tasks, completed, cancelled, elapsed_ms,
                              sequential_ms, timeout_ms, throughput)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mode,
                completed + cancelled,
                completed,
                cancelled,
                elapsed_ms,
                sequential_ms,
                timeout_ms,
                throughput,
            ),
        )
        run_id = cur.lastrowid
        if tasks:
            conn.executemany(
                """
                INSERT INTO run_tasks
                    (run_id, media_key, name, kind, status, delay_ms, started_at_ms, finished_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        t["key"],
                        t["name"],
                        t["kind"],
                        t["status"],
                        t["delay_ms"],
                        t["started_at_ms"],
                        t["finished_at_ms"],
                    )
                    for t in tasks
                ],
            )
    return int(run_id)


def list_runs(limit: int = 15) -> list[dict[str, Any]]:
    """Recent runs, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, mode, total_tasks, completed, cancelled, elapsed_ms,
                   sequential_ms, timeout_ms, throughput, created_at
            FROM runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]