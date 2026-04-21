"""
简报持久化（SQLite）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "vc_agent.db"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                markdown TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_briefs_id ON briefs(id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_items (
                url TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                score REAL NOT NULL,
                reason TEXT,
                raw_json TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_processed_at ON pipeline_items(processed_at)"
        )


def save_brief(brief_id: str, payload: Dict[str, Any], markdown: str) -> int:
    init_db()
    created = datetime.now(timezone.utc).isoformat()
    blob = json.dumps(payload, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO briefs (brief_id, created_at, payload_json, markdown) VALUES (?, ?, ?, ?)",
            (brief_id, created, blob, markdown),
        )
        conn.commit()
        return int(cur.lastrowid)


def upsert_pipeline_item(
    url: str,
    topic: str,
    score: float,
    reason: str,
    raw: Dict[str, Any],
    summary: Dict[str, Any],
) -> None:
    init_db()
    created = datetime.now(timezone.utc).isoformat()
    blob_raw = json.dumps(raw, ensure_ascii=False)
    blob_sm = json.dumps(summary, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_items (url, topic, score, reason, raw_json, summary_json, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                topic = excluded.topic,
                score = excluded.score,
                reason = excluded.reason,
                raw_json = excluded.raw_json,
                summary_json = excluded.summary_json,
                processed_at = excluded.processed_at
            """,
            (url, topic, score, reason or "", blob_raw, blob_sm, created),
        )
        conn.commit()


def list_pipeline_since(cutoff_iso: str) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT url, topic, score, reason, raw_json, summary_json, processed_at
            FROM pipeline_items
            WHERE processed_at >= ?
            ORDER BY score DESC
            """,
            (cutoff_iso,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "url": r["url"],
                "topic": r["topic"],
                "score": float(r["score"]),
                "reason": (r["reason"] or ""),
                "raw": json.loads(r["raw_json"]),
                "summary": json.loads(r["summary_json"]),
                "processed_at": r["processed_at"],
            }
        )
    return out
