"""
用户反馈与排序偏好（落盘 JSON + JSONL）。
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
FEEDBACK_PATH = DATA_DIR / "feedback.jsonl"
PREFS_PATH = DATA_DIR / "preferences.json"

_feedback_lock = threading.Lock()


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _domain_from_url(url: str) -> str:
    try:
        net = (urlparse(url).netloc or "").lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


def rebuild_preferences() -> Dict[str, Any]:
    ensure_data_dir()
    sources: Dict[str, float] = defaultdict(lambda: 1.0)
    authors: Dict[str, float] = defaultdict(lambda: 1.0)
    links: Dict[str, float] = defaultdict(lambda: 1.0)
    domains: Dict[str, float] = defaultdict(lambda: 1.0)

    dedupe_hours = float((os.getenv("FEEDBACK_DEDUPE_HOURS") or "12").strip() or "12")
    half_life_days = float((os.getenv("FEEDBACK_HALF_LIFE_DAYS") or "30").strip() or "30")
    dedupe_window_sec = max(0.0, dedupe_hours) * 3600.0
    half_life_sec = max(1.0, half_life_days) * 86400.0
    now = datetime.now(timezone.utc)
    seen_recent: Dict[tuple[str, str, str], datetime] = {}
    if FEEDBACK_PATH.exists():
        with FEEDBACK_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                vote = (rec.get("vote") or "").lower()
                item = (rec.get("item") or "").strip()
                src = (rec.get("source") or "").strip() or None
                auth = (rec.get("author") or "").strip() or None
                dom = _domain_from_url(item)
                ts_raw = str(rec.get("ts") or "").strip()
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if ts_raw else now
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except Exception:
                    ts = now
                age_sec = max(0.0, (now - ts).total_seconds())
                decay = math.exp(-math.log(2.0) * age_sec / half_life_sec)
                key = ((src or ""), (auth or ""), item)
                prev = seen_recent.get(key)
                if prev is not None and (ts - prev).total_seconds() <= dedupe_window_sec:
                    continue
                seen_recent[key] = ts
                if vote == "up":
                    if src:
                        sources[src] += 0.06 * decay
                    if auth:
                        authors[auth] += 0.06 * decay
                    if item:
                        links[item] += 0.1 * decay
                    if dom:
                        domains[dom] += 0.04 * decay
                elif vote == "down":
                    if src:
                        sources[src] -= 0.12 * decay
                    if auth:
                        authors[auth] -= 0.18 * decay
                    if item:
                        links[item] -= 0.45 * decay
                    if dom:
                        domains[dom] -= 0.06 * decay

    def clamp_map(m: Dict[str, float], lo: float, hi: float) -> Dict[str, float]:
        return {k: round(_clamp(v, lo, hi), 4) for k, v in m.items()}

    out = {
        "sources": clamp_map(dict(sources), 0.35, 1.55),
        "authors": clamp_map(dict(authors), 0.35, 1.55),
        "link_multiplier": clamp_map(dict(links), 0.12, 1.6),
        "domains": clamp_map(dict(domains), 0.5, 1.45),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with PREFS_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def load_preferences() -> Dict[str, Any]:
    ensure_data_dir()
    if not PREFS_PATH.exists():
        rebuild_preferences()
    try:
        with PREFS_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return rebuild_preferences()


def preference_multiplier(item_source: str, item_author: str, item_link: str, prefs: Dict[str, Any]) -> float:
    s = float((prefs.get("sources") or {}).get(item_source, 1.0))
    a = float((prefs.get("authors") or {}).get(item_author, 1.0))
    l = float((prefs.get("link_multiplier") or {}).get(item_link, 1.0))
    dom = _domain_from_url(item_link)
    d = float((prefs.get("domains") or {}).get(dom, 1.0)) if dom else 1.0
    return max(0.05, round(s * a * l * d, 4))


def append_feedback(
    item_url: str,
    vote: str,
    source: Optional[str] = None,
    author: Optional[str] = None,
    *,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    ensure_data_dir()
    vote = vote.lower().strip()
    if vote not in ("up", "down"):
        raise ValueError("vote 须为 up 或 down")
    rec: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "item": item_url.strip(),
        "vote": vote,
    }
    if source:
        rec["source"] = source.strip()
    if author:
        rec["author"] = author.strip()
    if meta:
        rec["meta"] = meta
    with _feedback_lock:
        with FEEDBACK_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        rebuild_preferences()
