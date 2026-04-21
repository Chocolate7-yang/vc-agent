"""
流水线服务层：统一暴露运行入口，兼容旧调用路径。
"""

from .agent import run, run_daily_brief, run_pipeline

__all__ = ["run_pipeline", "run_daily_brief", "run"]
