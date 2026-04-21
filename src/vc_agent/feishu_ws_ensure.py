"""
在推送飞书 interactive 卡片之前，确保 vc_agent.feishu_events 有且仅存一条后台长连接。

仅用 bash nohup & 拉起时，部分环境下 run.sh 结束后子进程会一并退出，导致卡片回调「未在线」；
改用 subprocess.Popen(..., start_new_session=True) 与当前 Python 进程脱钩，shell 退出后仍常驻。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

LOG = logging.getLogger("vc_agent.feishu_ws")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _feishu_events_running() -> bool:
    try:
        r = subprocess.run(
            ["pgrep", "-f", r"vc_agent\.feishu_events"],
            capture_output=True,
            timeout=5,
        )
        if r.returncode == 0:
            return True
        r2 = subprocess.run(
            ["pgrep", "-f", "python.*feishu_events"],
            capture_output=True,
            timeout=5,
        )
        return r2.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def ensure_feishu_events_before_card_push() -> None:
    if not ((os.getenv("FEISHU_APP_ID") or "").strip() and (os.getenv("FEISHU_APP_SECRET") or "").strip()):
        return
    if _feishu_events_running():
        LOG.debug("feishu_events 已在运行，跳过拉起")
        return

    root = _project_root()
    log_path = root / "logs" / "feishu_events.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    src = str(root / "src")
    pp = env.get("PYTHONPATH", "")
    if src not in pp.split(os.pathsep):
        env["PYTHONPATH"] = f"{src}{os.pathsep}{pp}" if pp else src

    LOG.info("正在启动 feishu_events（长连接 card.action.trigger，日志 %s）…", log_path)
    log_f = open(log_path, "a", encoding="utf-8")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "vc_agent.feishu_events"],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_f.close()

    wait = 4.0
    try:
        wait = float((os.getenv("FEISHU_WS_SPAWN_WAIT_SEC") or "4").strip())
    except ValueError:
        wait = 4.0
    wait = max(1.0, min(wait, 60.0))
    time.sleep(wait)
    if _feishu_events_running():
        LOG.info("feishu_events 已就绪（可对卡片 👍/👎 回调）")
    else:
        LOG.warning(
            "feishu_events 拉起后未检测到进程，请点击卡片仍异常时请单独运行: PYTHONPATH=src %s -m vc_agent.feishu_events",
            sys.executable,
        )
