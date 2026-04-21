#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .pipeline_service import run_daily_brief, run_pipeline

LOG = logging.getLogger("vc_agent.scheduler")


def _env_bool(name: str, default: bool) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


def configure_logging() -> None:
    quiet = _env_bool("VC_AGENT_QUIET", False)
    level_name = (os.getenv("LOG_LEVEL") or ("WARNING" if quiet else "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%Y-%m-%d %H:%M:%S", force=True)
    if quiet:
        for name in ("apscheduler", "apscheduler.scheduler", "apscheduler.executors", "apscheduler.jobstores"):
            logging.getLogger(name).setLevel(logging.WARNING)
        LOG.setLevel(logging.WARNING)


def run_with_retry(fn: Callable[[], None], job_id: str) -> None:
    max_retries = max(1, min(int(os.getenv("JOB_MAX_RETRIES", "3")), 10))
    base_delay = max(5.0, float(os.getenv("JOB_RETRY_BASE_SECONDS", "60")))
    last_exc: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            LOG.info("job %s 开始 (attempt %s/%s)", job_id, attempt, max_retries)
            fn()
            LOG.info("job %s 成功", job_id)
            return
        except Exception as exc:
            last_exc = exc
            LOG.exception("job %s 失败 (attempt %s/%s): %s", job_id, attempt, max_retries, exc)
            if attempt < max_retries:
                wait = base_delay * (2 ** (attempt - 1))
                LOG.info("job %s %.0f 秒后重试 …", job_id, wait)
                time.sleep(wait)
    LOG.error("job %s 已达最大重试次数，放弃: %s", job_id, last_exc)


def main() -> None:
    configure_logging()
    interval_hours = float(os.getenv("PIPELINE_INTERVAL_HOURS", "4"))
    if interval_hours <= 0:
        LOG.error("PIPELINE_INTERVAL_HOURS 须为正数")
        sys.exit(1)
    brief_hour = int(os.getenv("BRIEF_HOUR", "7")) % 24
    brief_minute = int(os.getenv("BRIEF_MINUTE", "0")) % 60
    test_brief_hour = (os.getenv("TEST_BRIEF_HOUR") or "").strip()
    test_brief_minute = (os.getenv("TEST_BRIEF_MINUTE") or "").strip()
    tz_name = (os.getenv("BRIEF_TZ") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    scheduler = BlockingScheduler(timezone=tz_name)
    scheduler.add_job(
        lambda: run_with_retry(run_pipeline, "pipeline"),
        IntervalTrigger(hours=interval_hours),
        id="vc_agent_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        lambda: run_with_retry(run_daily_brief, "daily_brief"),
        CronTrigger(hour=brief_hour, minute=brief_minute, timezone=tz_name),
        id="vc_agent_daily_brief",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if test_brief_hour:
        try:
            t_hour = int(test_brief_hour) % 24
            t_min = int(test_brief_minute or "0") % 60
            scheduler.add_job(
                lambda: run_with_retry(run_daily_brief, "daily_brief_test"),
                CronTrigger(hour=t_hour, minute=t_min, timezone=tz_name),
                id="vc_agent_daily_brief_test",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            LOG.info("已启用测试简报时刻: %02d:%02d (%s)", t_hour, t_min, tz_name)
        except ValueError:
            LOG.warning("忽略无效 TEST_BRIEF_HOUR/MINUTE: %r:%r", test_brief_hour, test_brief_minute)
    if _env_bool("RUN_PIPELINE_ON_START", True):
        try:
            run_with_retry(run_pipeline, "pipeline_start")
        except Exception:
            traceback.print_exc()
    if _env_bool("RUN_DAILY_ON_START", False):
        try:
            run_with_retry(run_daily_brief, "daily_brief_start")
        except Exception:
            traceback.print_exc()
    scheduler.start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOG.info("收到 Ctrl+C，调度器已停止。")
