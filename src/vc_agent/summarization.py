"""
摘要层：LLM 摘要、同栏合并、核心洞察生成。
"""

from .agent import (
    BriefRow,
    build_merged_brief_rows,
    llm_daily_core_insights,
    llm_merge_topic_cluster,
    llm_summarize,
)

__all__ = [
    "BriefRow",
    "llm_summarize",
    "llm_merge_topic_cluster",
    "build_merged_brief_rows",
    "llm_daily_core_insights",
]
