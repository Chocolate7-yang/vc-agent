#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from urllib import error, request
from urllib.parse import urlencode


def _ssl_ctx():
    import ssl

    v = (os.getenv("ALLOW_INSECURE_SSL") or "false").lower()
    if v in {"1", "true", "yes", "on"}:
        return ssl._create_unverified_context()
    return None


def list_bot_chats(
    tenant_access_token: str, *, page_size: int = 50, page_token: Optional[str] = None
) -> Dict[str, Any]:
    """GET im/v1/chats 单页；存在下一页时 data 含 has_more / page_token。"""
    ps = min(max(1, page_size), 100)
    q: Dict[str, Any] = {"page_size": ps}
    if page_token:
        q["page_token"] = page_token
    url = "https://open.feishu.cn/open-apis/im/v1/chats?" + urlencode(q)
    req = request.Request(url, headers={"Authorization": f"Bearer {tenant_access_token}"}, method="GET")
    try:
        with request.urlopen(req, timeout=25, context=_ssl_ctx()) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"获取群列表 HTTP {e.code}: {raw[:2000]}") from e
    out = json.loads(raw)
    if out.get("code") != 0:
        raise RuntimeError(f"获取群列表失败: {out}")
    return out.get("data") or {}


def collect_bot_chats(tenant_access_token: str, *, page_size: int = 50) -> List[Dict[str, Any]]:
    """分页拉取机器人可见的群会话，按 chat_id 去重（顺序保留首次）。"""
    rows: List[Dict[str, Any]] = []
    tok: Optional[str] = None
    for _ in range(500):
        data = list_bot_chats(tenant_access_token, page_size=page_size, page_token=tok)
        items = data.get("items") or []
        for it in items:
            if isinstance(it, dict):
                rows.append(it)
        if not data.get("has_more"):
            break
        tok = data.get("page_token")
        if not tok:
            break
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for it in rows:
        cid = str(it.get("chat_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(it)
    return out


def collect_bot_chat_ids(tenant_access_token: str, *, page_size: int = 50) -> List[str]:
    return [str(x.get("chat_id") or "").strip() for x in collect_bot_chats(tenant_access_token, page_size=page_size)]


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(req, timeout=20, context=_ssl_ctx()) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    data = json.loads(raw)
    if data.get("code") != 0:
        raise RuntimeError(f"tenant_access_token 失败: {data}")
    tok = data.get("tenant_access_token")
    if not tok:
        raise RuntimeError("响应无 tenant_access_token")
    return str(tok)


def send_text_message(
    *,
    tenant_access_token: str,
    receive_id: str,
    receive_id_type: str,
    text: str,
) -> Dict[str, Any]:
    """发送纯文本消息（适合附带云文档链接等可转发内容）。"""
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30, context=_ssl_ctx()) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"发送消息 HTTP {e.code}: {raw[:800]}") from e
    out = json.loads(raw)
    if out.get("code") != 0:
        raise RuntimeError(f"发送消息失败: {out}")
    return out


def send_text_from_env(text: str) -> Dict[str, Any]:
    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    receive_id = (os.getenv("FEISHU_RECEIVE_ID") or "").strip()
    receive_id_type = (os.getenv("FEISHU_RECEIVE_ID_TYPE") or "chat_id").strip()
    if not app_id or not app_secret or not receive_id:
        raise ValueError("需设置 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_RECEIVE_ID")
    tok = get_tenant_access_token(app_id, app_secret)
    return send_text_message(
        tenant_access_token=tok,
        receive_id=receive_id,
        receive_id_type=receive_id_type,
        text=text,
    )


def send_interactive_message(
    *,
    tenant_access_token: str,
    receive_id: str,
    receive_id_type: str,
    card: Dict[str, Any],
) -> Dict[str, Any]:
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    payload = {"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)}
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30, context=_ssl_ctx()) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"发送消息 HTTP {e.code}: {raw[:800]}") from e
    out = json.loads(raw)
    if out.get("code") != 0:
        raise RuntimeError(f"发送消息失败: {out}")
    return out


def send_interactive_from_env(card: Dict[str, Any]) -> Dict[str, Any]:
    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    receive_id = (os.getenv("FEISHU_RECEIVE_ID") or "").strip()
    receive_id_type = (os.getenv("FEISHU_RECEIVE_ID_TYPE") or "chat_id").strip()
    if not app_id or not app_secret or not receive_id:
        raise ValueError("需设置 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_RECEIVE_ID")
    tok = get_tenant_access_token(app_id, app_secret)
    return send_interactive_message(
        tenant_access_token=tok,
        receive_id=receive_id,
        receive_id_type=receive_id_type,
        card=card,
    )
