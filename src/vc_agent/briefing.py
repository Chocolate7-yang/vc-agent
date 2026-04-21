"""
简报层：Markdown 与 JSON 输出构建。
"""

from .agent import build_brief_payload, compose_markdown, write_brief_latest_json

__all__ = ["build_brief_payload", "write_brief_latest_json", "compose_markdown"]
