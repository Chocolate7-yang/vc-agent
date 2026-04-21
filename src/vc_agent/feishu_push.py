#!/usr/bin/env python3
"""
飞书应用模式推送：在每日简报生成成功后推送 interactive 卡片（折叠分栏 + Markdown）。
环境变量：
  FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_RECEIVE_ID  应用发消息必需项
  FEISHU_RECEIVE_ID_TYPE  可选，默认 chat_id
  FEISHU_PUSH_ON_BOOTSTRAP 默认 0：run.sh 7x24 启动时同步跑的简报不推送（避免非 07:00 误发）；设为 1 则启动简报也推送
推送失败仅打日志，不影响简报落盘与后续调度。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("vc_agent.feishu")

# 飞书自定义机器人单条请求体上限约 20KB，留出余量
_MAX_BODY_BYTES = 18_500


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name, default)
    return (v or "").strip() or None


def _truncate(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _escape_md_line(s: str) -> str:
    """弱化特殊字符对卡片 Markdown 的破坏。"""
    return (s or "").replace("`", "'").replace("<", "＜")


def _topic_panel_title(topic: str, shown: int, total: int) -> str:
    icons = {"AI": "🤖 AI", "芯片": "⚡ 芯片", "机器人": "🦾 机器人"}
    t = icons.get(topic, topic)
    return f"{t}（{shown}/{total} 条）"


def _build_section_markdown(
    section: Dict[str, Any],
    *,
    per_item_content_max: int,
) -> str:
    lines: List[str] = []
    items = section.get("items") or []
    for i, it in enumerate(items, 1):
        title = _escape_md_line(str(it.get("title") or ""))
        content = _truncate(_escape_md_line(str(it.get("content") or "")), per_item_content_max)
        signal = _escape_md_line(str(it.get("signal") or ""))
        url = str(it.get("url") or "").strip()
        lines.append(f"**{i}. {title}**")
        if content:
            lines.append(content)
        if signal:
            lines.append(f"**投资信号：** {signal}")
        if url:
            lines.append(f"[👉 打开原文]({url})")
        lines.append("")
    return "\n".join(lines).strip() or "（本栏暂无条目）"


def _card_body_elements(
    payload: Dict[str, Any],
    *,
    per_item_content_max: int,
) -> List[Dict[str, Any]]:
    date = str(payload.get("date") or "")
    brief_id = str(payload.get("brief_id") or "")
    insights: List[str] = list(payload.get("insights") or [])[:3]
    stats = payload.get("stats") or {}

    insights_md = "\n".join(f"- {_escape_md_line(x)}" for x in insights if str(x).strip())
    if not insights_md:
        insights_md = "（暂无）"

    mon = stats.get("monitored_total", 0)
    passed = stats.get("passed_count", 0)
    pref = _truncate(_escape_md_line(str(stats.get("pref_hint") or "")), 120)

    intro = (
        f"**日期：** {date}\n"
        f"**简报 ID：** `{brief_id}`\n\n"
        f"**📈 今日核心洞察**\n{insights_md}\n\n"
        f"**📊 数据：** 监测 {mon} 条 · 入选 {passed} 条\n"
        f"{pref}\n\n"
        "下方按赛道折叠，点击展开阅读详情（与本地 Markdown 简报一致）。"
    )

    elements: List[Dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": intro,
            "text_align": "left",
            "text_size": "normal_v2",
            "margin": "0px 0px 8px 0px",
        }
    ]

    sections = payload.get("sections") or []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        topic = str(sec.get("topic") or "")
        heading = _topic_panel_title(topic, int(sec.get("shown") or 0), int(sec.get("total") or 0))
        body_md = _build_section_markdown(sec, per_item_content_max=per_item_content_max)
        elements.append(
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                    "title": {"tag": "plain_text", "content": heading},
                    "vertical_align": "center",
                },
                "border": {"color": "grey", "corner_radius": "5px"},
                "vertical_spacing": "8px",
                "padding": "8px 8px 8px 8px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": body_md,
                        "text_align": "left",
                        "text_size": "normal_v2",
                    }
                ],
            }
        )

    return elements


def build_interactive_message(payload: Dict[str, Any], *, per_item_content_max: int) -> Dict[str, Any]:
    """构造单条 interactive 消息体（含 msg_type）。"""
    date = str(payload.get("date") or "")
    brief_id = str(payload.get("brief_id") or "")
    title = f"每日投资信息简报 · VC Agent · {date}"
    subtitle = f"{brief_id} · 简报"

    card = {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "wide_screen_mode": True,
        },
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": _truncate(title, 80)},
            "subtitle": {"tag": "plain_text", "content": _truncate(subtitle, 100)},
            "padding": "12px 12px 12px 12px",
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": _card_body_elements(payload, per_item_content_max=per_item_content_max),
        },
    }
    return {"msg_type": "interactive", "card": card}


def _message_bytes(msg: Dict[str, Any]) -> int:
    return len(json.dumps(msg, ensure_ascii=False).encode("utf-8"))


def _ensure_logging() -> None:
    if not logging.root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            force=True,
        )


def push_daily_brief_to_feishu(
    web_payload: Dict[str, Any],
    md_text: str,
    *,
    brief_id: str,
    md_path: Path,
) -> None:
    """
    简报已成功写入磁盘后调用；失败仅记录日志。
    md_text 与 web_payload 同源；推送以结构化卡片呈现，内容与 Markdown 简报一致。
    """
    _ensure_logging()
    has_app = bool(_env("FEISHU_APP_ID") and _env("FEISHU_APP_SECRET") and _env("FEISHU_RECEIVE_ID"))
    if not has_app:
        LOG.debug("未配置 FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_RECEIVE_ID，跳过飞书推送")
        return

    if (_env("VC_AGENT_BOOTSTRAP_BRIEF") or "").lower() in {"1", "true", "yes", "on"}:
        if (_env("FEISHU_PUSH_ON_BOOTSTRAP") or "0").strip().lower() not in {"1", "true", "yes", "on"}:
            LOG.debug(
                "启动引导简报跳过飞书（定时任务仍会推送；需要此处推送请设 FEISHU_PUSH_ON_BOOTSTRAP=1）"
            )
            return

    _ = md_text  # 与简报文件一致已由上游写入；推送用 web_payload 结构化渲染

    # 逐步压缩单条「内容总结」直至整体低于上限
    msg: Optional[Dict[str, Any]] = None
    for cap in (360, 280, 220, 180, 140, 100):
        candidate = build_interactive_message(web_payload, per_item_content_max=cap)
        if _message_bytes(candidate) <= _MAX_BODY_BYTES:
            msg = candidate
            break
    if msg is None:
        msg = build_interactive_message(web_payload, per_item_content_max=80)

    try:
        from .feishu_app_send import send_interactive_from_env

        app_card = msg.get("card")
        if not isinstance(app_card, dict):
            raise RuntimeError("interactive 消息缺少 card 字段")
        resp = send_interactive_from_env(app_card)
        data = resp.get("data") or {}
        msg_id = data.get("message_id") or data.get("messageId") or ""
        LOG.info(
            "飞书推送成功（应用发消息）：brief_id=%s，本地文件=%s，message_id=%s",
            brief_id,
            md_path,
            msg_id,
        )
        return
    except Exception as exc:
        LOG.error(
            "飞书推送失败（已保留本地 Markdown：%s）：%s",
            md_path,
            exc,
            exc_info=True,
        )
        print(f"[WARN] 飞书推送失败: {exc}", file=sys.stderr)
