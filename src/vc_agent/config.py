"""
项目根目录与通用配置：路径解析、环境变量、SSL（与 urllib 请求共用）。
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path
from typing import Optional


def project_root() -> Path:
    """包位于 src/vc_agent/，项目根为其上两级目录。"""
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = project_root()
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
TEMPLATES_DIR = PROJECT_ROOT / "templates"


def load_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    if value is None:
        return None
    return value.strip()


def env_quiet() -> bool:
    """VC_AGENT_QUIET=1 时精简终端输出（run.sh 默认开启）。"""
    v = (os.getenv("VC_AGENT_QUIET") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def allow_insecure_ssl() -> bool:
    val = (load_env("ALLOW_INSECURE_SSL", "false") or "false").lower()
    return val in {"1", "true", "yes", "on"}


def build_ssl_context() -> Optional[ssl.SSLContext]:
    if allow_insecure_ssl():
        return ssl._create_unverified_context()
    return None
