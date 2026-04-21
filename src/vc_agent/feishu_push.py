#!/usr/bin/env python3
"""
飞书应用模式推送：在每日简报生成成功后向群聊发消息。

- 默认（FEISHU_PUSH_MODE=card）：interactive 卡片（折叠分栏 + Markdown；含原文链接时有 👍/👎）。
- FEISHU_PUSH_MODE=doc：创建飞书云文档并发送文本消息中的文档链接（便于对外分享，无卡片回调）。
- FEISHU_PUSH_MODE=both：先云文档链接，再发卡片。

接收方：`FEISHU_APP_ID` / `FEISHU_APP_SECRET` 配好后，**未填** `FEISHU_RECEIVE_ID` 时向机器人所在全部群推送
（与 `feishu_list_chats` 同源接口拉会话列表）；若填写 `FEISHU_RECEIVE_ID` 则只向该会话发（单群或 `open_id` 单聊）。

推送失败仅打日志，不影响简报落盘与后续调度。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

LOG = logging.getLogger("vc_agent.feishu")

# 与 feishu_events.do_card_action_trigger / _apply_feedback 约定一致
_FeedbackDetail = Literal["full", "minimal", "none"]


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name, default)
    return (v or "").strip() or None


def _feishu_push_max_chats() -> int:
    raw = (_env("FEISHU_PUSH_MAX_CHATS") or "100").strip()
    try:
        n = int(raw or "100")
    except ValueError:
        n = 100
    return max(1, min(n, 500))


def _feishu_push_mode() -> str:
    """card：仅交互卡片（默认）；doc：仅云文档 + 文本链接；both：先云文档再卡片。"""
    m = ((_env("FEISHU_PUSH_MODE") or "card")).strip().lower()
    if m in {"card", "doc", "both"}:
        return m
    return "card"


def _escape_md_line(s: str) -> str:
    """弱化特殊字符对卡片 Markdown 的破坏。"""
    return (s or "").replace("`", "'").replace("<", "＜")


def _topic_panel_title(topic: str, shown: int, total: int) -> str:
    icons = {"AI": "🤖 AI", "芯片": "⚡ 芯片", "机器人": "🦾 机器人"}
    t = icons.get(topic, topic)
    return f"{t}（{shown}/{total} 条）"


def _item_primary_url(it: Dict[str, Any]) -> str:
    """简报条目主链接：优先 url，否则取 urls 列表首条非空（与 build_brief_payload 一致）。"""
    u = str(it.get("url") or "").strip()
    if u:
        return u
    raw = it.get("urls")
    if isinstance(raw, list):
        for x in raw:
            s = str(x or "").strip()
            if s:
                return s
    return ""


def _build_section_markdown(section: Dict[str, Any]) -> str:
    lines: List[str] = []
    items = section.get("items") or []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            it = {}
        title = _escape_md_line(str(it.get("title") or ""))
        content = _escape_md_line(str(it.get("content") or ""))
        signal = _escape_md_line(str(it.get("signal") or ""))
        url = _item_primary_url(it)
        lines.append(f"**{i}. {title}**")
        if content:
            lines.append(content)
        if signal:
            lines.append(f"**投资信号：** {signal}")
        if url:
            lines.append(f"[👉 打开原文]({url})")
        lines.append("")
    return "\n".join(lines).strip() or "（本栏暂无条目）"


def _one_item_markdown(it: Dict[str, Any], index_1based: int) -> str:
    if not isinstance(it, dict):
        it = {}
    title = _escape_md_line(str(it.get("title") or ""))
    content = _escape_md_line(str(it.get("content") or ""))
    signal = _escape_md_line(str(it.get("signal") or ""))
    url = _item_primary_url(it)
    lines: List[str] = [f"**{index_1based}. {title}**"]
    if content:
        lines.append(content)
    if signal:
        lines.append(f"**投资信号：** {signal}")
    if url:
        lines.append(f"[👉 打开原文]({url})")
    return "\n".join(lines).strip() or f"**{index_1based}.**（条目无正文）"


def _feedback_callback_payload(
    vote: str,
    item_url: str,
    source: str,
    author: str,
    *,
    detail: _FeedbackDetail,
) -> Dict[str, Any]:
    """card.action.trigger 回传 JSON，供 feishu_events._parse_action_value 使用。"""
    p: Dict[str, Any] = {"vote": vote, "item": (item_url or "").strip()}
    if detail != "full":
        # minimal / none：仅 vote + item（偏好学习仍主要依赖链接维度）
        return p
    s = (source or "").strip()
    a = (author or "").strip()
    if s:
        p["source"] = s
    if a:
        p["author"] = a
    return p


def _feedback_button_pair(
    *,
    pair_id: int,
    val_up: Dict[str, Any],
    val_down: Dict[str, Any],
) -> Dict[str, Any]:
    """同一行 👍 / 👎，element_id 在卡片内唯一（≤20 字符）。"""
    base = f"i{pair_id:05d}"
    return {
        "tag": "column_set",
        "margin": "0px 0px 10px 0px",
        "flex_mode": "flow",
        "background_style": "default",
        "horizontal_spacing": "8px",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "vertical_align": "center",
                "elements": [
                    {
                        "tag": "button",
                        "element_id": f"{base}u",
                        "type": "default",
                        "size": "tiny",
                        "text": {"tag": "plain_text", "content": "👍"},
                        "behaviors": [{"type": "callback", "value": val_up}],
                    }
                ],
            },
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "vertical_align": "center",
                "elements": [
                    {
                        "tag": "button",
                        "element_id": f"{base}d",
                        "type": "default",
                        "size": "tiny",
                        "text": {"tag": "plain_text", "content": "👎"},
                        "behaviors": [{"type": "callback", "value": val_down}],
                    }
                ],
            },
        ],
    }


def _collapsible_inner_elements(
    section: Dict[str, Any],
    *,
    feedback_detail: _FeedbackDetail,
    pair_id_start: int,
) -> tuple[List[Dict[str, Any]], int]:
    """折叠面板内元素；返回 (elements, 下一条全局 pair 序号)。feedback_detail=none 时为整段 Markdown、无按钮。"""
    items = section.get("items") or []
    if not items:
        return (
            [
                {
                    "tag": "markdown",
                    "content": "（本栏暂无条目）",
                    "text_align": "left",
                    "text_size": "normal_v2",
                }
            ],
            pair_id_start,
        )

    if feedback_detail == "none":
        body_md = _build_section_markdown(section)
        return (
            [
                {
                    "tag": "markdown",
                    "content": body_md,
                    "text_align": "left",
                    "text_size": "normal_v2",
                }
            ],
            pair_id_start,
        )

    out: List[Dict[str, Any]] = []
    pair_id = pair_id_start
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            it = {}
        md = _one_item_markdown(it, i)
        out.append(
            {
                "tag": "markdown",
                "content": md,
                "text_align": "left",
                "text_size": "normal_v2",
                "margin": "0px 0px 4px 0px",
            }
        )
        url = _item_primary_url(it)
        source = str(it.get("source") or "").strip()
        author = str(it.get("author") or "").strip()
        if url:
            vu = _feedback_callback_payload("up", url, source, author, detail=feedback_detail)
            vd = _feedback_callback_payload("down", url, source, author, detail=feedback_detail)
            out.append(_feedback_button_pair(pair_id=pair_id, val_up=vu, val_down=vd))
            pair_id += 1
    return out, pair_id


def _card_body_elements(
    payload: Dict[str, Any],
    *,
    feedback_detail: _FeedbackDetail = "full",
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
    pref = _escape_md_line(str(stats.get("pref_hint") or ""))

    intro = (
        f"**日期：** {date}\n"
        f"**简报 ID：** `{brief_id}`\n\n"
        f"**📈 今日核心洞察**\n{insights_md}\n\n"
        f"**📊 数据：** 监测 {mon} 条 · 入选 {passed} 条\n"
        f"{pref}\n\n"
        "下方按赛道折叠；展开后每条下有 👍/👎，点击即记入偏好。"
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
    pair_next = 0
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        topic = str(sec.get("topic") or "")
        heading = _topic_panel_title(topic, int(sec.get("shown") or 0), int(sec.get("total") or 0))
        inner, pair_next = _collapsible_inner_elements(
            sec,
            feedback_detail=feedback_detail,
            pair_id_start=pair_next,
        )
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
                "elements": inner,
            }
        )

    return elements


def build_interactive_message(
    payload: Dict[str, Any],
    *,
    feedback_detail: _FeedbackDetail = "full",
) -> Dict[str, Any]:
    """构造单条 interactive 消息体（含 msg_type）；不对正文与卡片头图做长度压缩。"""
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
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "padding": "12px 12px 12px 12px",
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": _card_body_elements(
                payload,
                feedback_detail=feedback_detail,
            ),
        },
    }
    return {"msg_type": "interactive", "card": card}


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
    md_text 与 web_payload 同源；卡片模式以结构化卡片呈现；云文档模式将 md_text 写入新建 docx。
    """
    _ensure_logging()
    has_app = bool(_env("FEISHU_APP_ID") and _env("FEISHU_APP_SECRET"))
    if not has_app:
        LOG.debug("未配置 FEISHU_APP_ID/FEISHU_APP_SECRET，跳过飞书推送")
        return

    if (_env("VC_AGENT_BOOTSTRAP_BRIEF") or "").lower() in {"1", "true", "yes", "on"}:
        if (_env("FEISHU_PUSH_ON_BOOTSTRAP") or "0").strip().lower() not in {"1", "true", "yes", "on"}:
            LOG.debug(
                "启动引导简报跳过飞书（定时任务仍会推送；需要此处推送请设 FEISHU_PUSH_ON_BOOTSTRAP=1）"
            )
            return

    mode = _feishu_push_mode()

    try:
        from .feishu_app_send import (
            collect_bot_chats,
            get_tenant_access_token,
            send_interactive_message,
            send_text_message,
        )

        date = str(web_payload.get("date") or "")
        app_id = (_env("FEISHU_APP_ID") or "").strip()
        app_secret = (_env("FEISHU_APP_SECRET") or "").strip()
        if not app_id or not app_secret:
            raise ValueError("飞书推送需要 FEISHU_APP_ID / FEISHU_APP_SECRET")
        tok = get_tenant_access_token(app_id, app_secret)

        receive_id_type = (_env("FEISHU_RECEIVE_ID_TYPE") or "chat_id").strip() or "chat_id"
        targets: List[tuple[str, str, str]] = []
        rid = (_env("FEISHU_RECEIVE_ID") or "").strip()
        if rid:
            targets.append((rid, receive_id_type, ""))
            LOG.info("飞书推送：单会话 FEISHU_RECEIVE_ID（%s）", receive_id_type)
        else:
            chats = collect_bot_chats(tok, page_size=100)
            cap = _feishu_push_max_chats()
            if len(chats) > cap:
                LOG.warning("机器人可见群数 %s 超过 FEISHU_PUSH_MAX_CHATS=%s，仅推送前 %s 个", len(chats), cap, cap)
                chats = chats[:cap]
            for it in chats:
                cid = str(it.get("chat_id") or "").strip()
                if cid:
                    name = str(it.get("name") or "").strip()
                    targets.append((cid, "chat_id", name))
            if not targets:
                LOG.warning(
                    "未配置 FEISHU_RECEIVE_ID 但未获取到任何群会话（请先将机器人拉进群并检查开放平台群列表权限）"
                )
                return
            LOG.info("飞书推送：机器人所在全部群，共 %s 个会话", len(targets))

        doc_msg_ids: List[str] = []
        card_msg_ids: List[str] = []

        if mode in {"doc", "both"}:
            from .feishu_docx import create_docx_from_markdown

            title = f"每日投资信息简报 · VC Agent · {date}"[:800]
            folder = _env("FEISHU_DOC_FOLDER_TOKEN")
            _, doc_url = create_docx_from_markdown(
                tok,
                title=title,
                markdown=md_text,
                folder_token=folder,
            )
            doc_line = (
                "📄 今日简报已写入飞书云文档，可复制链接分享（组织外可见请在文档右上角「分享」中单独开启）。\n"
                f"{doc_url}"
            )
            for recv_id, recv_type, gname in targets:
                try:
                    r_doc = send_text_message(
                        tenant_access_token=tok,
                        receive_id=recv_id,
                        receive_id_type=recv_type,
                        text=doc_line,
                    )
                    d_doc = r_doc.get("data") or {}
                    doc_msg_ids.append(str(d_doc.get("message_id") or d_doc.get("messageId") or ""))
                    LOG.info("飞书云文档链接已发送至 chat=%s name=%r", recv_id, gname or "?")
                except Exception as exc_one:
                    LOG.error("飞书文本（云文档链接）发送至 %s 失败: %s", recv_id, exc_one, exc_info=True)

        if mode in {"card", "both"}:
            msg = build_interactive_message(web_payload, feedback_detail="full")
            from .feishu_ws_ensure import ensure_feishu_events_before_card_push

            ensure_feishu_events_before_card_push()
            app_card = msg.get("card")
            if not isinstance(app_card, dict):
                raise RuntimeError("interactive 消息缺少 card 字段")
            for recv_id, recv_type, gname in targets:
                try:
                    resp = send_interactive_message(
                        tenant_access_token=tok,
                        receive_id=recv_id,
                        receive_id_type=recv_type,
                        card=app_card,
                    )
                    data = resp.get("data") or {}
                    card_msg_ids.append(str(data.get("message_id") or data.get("messageId") or ""))
                    LOG.info("飞书卡片已发送至 chat=%s name=%r", recv_id, gname or "?")
                except Exception as exc_one:
                    LOG.error("飞书卡片发送至 %s 失败: %s", recv_id, exc_one, exc_info=True)

        LOG.info(
            "飞书推送完成（应用发消息）：brief_id=%s，本地文件=%s，mode=%s，"
            "targets=%s，doc_message_ids=%s，card_message_ids=%s",
            brief_id,
            md_path,
            mode,
            len(targets),
            doc_msg_ids,
            card_msg_ids,
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
