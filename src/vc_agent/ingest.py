"""
采集层：对外暴露 RSS 采集与解析能力。
当前实现先复用 agent.py 中的稳定逻辑，后续可独立演进。
"""

from .agent import (
    RawItem,
    fetch_youtube_channel_rss,
    load_youtube_channel_registry,
    parse_any_feed,
    parse_atom_feed,
    parse_rss2_channel,
)

__all__ = [
    "RawItem",
    "load_youtube_channel_registry",
    "fetch_youtube_channel_rss",
    "parse_atom_feed",
    "parse_rss2_channel",
    "parse_any_feed",
]
