#!/usr/bin/env python3
"""
飞书企业自建应用：长连接（WSS）接收 card.action.trigger，异步写入本地反馈库（preferences）。
"""

from __future__ import annotations

import base64
import http
import json
import logging
import os
import ssl
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict


def _load_dotenv_from_project() -> None:
    for base in (Path(__file__).resolve().parents[2], Path.cwd()):
        path = base / ".env"
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.lower().startswith("export "):
                s = s[7:].strip()
            if "=" not in s:
                continue
            key, _, val = s.partition("=")
            key = key.strip()
            if not key or key in os.environ:
                continue
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            os.environ[key] = val
        return


def _apply_allow_insecure_ssl() -> None:
    v = (os.getenv("ALLOW_INSECURE_SSL") or "false").lower()
    if v not in {"1", "true", "yes", "on"}:
        return
    warnings.filterwarnings("ignore", message="Unverified HTTPS request", category=Warning)
    import requests
    import websockets

    _orig_post = requests.post
    _orig_ws_connect = websockets.connect

    def _post(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("verify", False)
        return _orig_post(*args, **kwargs)

    async def _ws_connect(uri: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("ssl") is None and str(uri).lower().startswith("wss"):
            kwargs = dict(kwargs)
            kwargs["ssl"] = ssl._create_unverified_context()
        return await _orig_ws_connect(uri, *args, **kwargs)

    requests.post = _post  # type: ignore[assignment]
    websockets.connect = _ws_connect  # type: ignore[assignment]


_load_dotenv_from_project()
_apply_allow_insecure_ssl()

from lark_oapi.core.const import UTF_8
from lark_oapi.core.enum import LogLevel
from lark_oapi.core.json import JSON
from lark_oapi.core.log import logger
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws.client import Client, _get_by_key
from lark_oapi.ws.const import (
    HEADER_BIZ_RT,
    HEADER_MESSAGE_ID,
    HEADER_SEQ,
    HEADER_SUM,
    HEADER_TRACE_ID,
    HEADER_TYPE,
)
from lark_oapi.ws.enum import MessageType
from lark_oapi.ws.model import Response

from .preferences import append_feedback

LOG = logging.getLogger("vc_agent.feishu_ws")
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu_fb")


def _patch_lark_ws_card_callback() -> None:
    if getattr(Client._handle_data_frame, "_vc_agent_patched", False):
        return

    async def _handle_data_frame(self: Any, frame: Any) -> None:
        hs = frame.headers
        msg_id = _get_by_key(hs, HEADER_MESSAGE_ID)
        trace_id = _get_by_key(hs, HEADER_TRACE_ID)
        sum_ = _get_by_key(hs, HEADER_SUM)
        seq = _get_by_key(hs, HEADER_SEQ)
        type_ = _get_by_key(hs, HEADER_TYPE)
        pl = frame.payload
        if int(sum_) > 1:
            pl = self._combine(msg_id, int(sum_), int(seq), pl)
            if pl is None:
                return
        message_type = MessageType(type_)
        logger.debug(
            self._fmt_log(
                "receive message, message_type: {}, message_id: {}, trace_id: {}, payload: {}",
                message_type.value,
                msg_id,
                trace_id,
                pl.decode(UTF_8),
            )
        )
        resp = Response(code=http.HTTPStatus.OK)
        try:
            start = int(round(time.time() * 1000))
            if message_type in (MessageType.EVENT, MessageType.CARD):
                result = self._event_handler.do_without_validation(pl)
            else:
                return
            end = int(round(time.time() * 1000))
            header = hs.add()
            header.key = HEADER_BIZ_RT
            header.value = str(end - start)
            if result is not None:
                resp.data = base64.b64encode(JSON.marshal(result).encode(UTF_8))
        except Exception:
            resp = Response(code=http.HTTPStatus.INTERNAL_SERVER_ERROR)
        frame.payload = JSON.marshal(resp).encode(UTF_8)
        await self._write_message(frame.SerializeToString())

    _handle_data_frame._vc_agent_patched = True  # type: ignore[attr-defined]
    Client._handle_data_frame = _handle_data_frame  # type: ignore[assignment]


def _parse_action_value(val: Any) -> Dict[str, Any]:
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return {}
    return {}


def _apply_feedback(payload: Dict[str, Any], operator: Any) -> None:
    vote = (payload.get("vote") or "").strip().lower()
    item = (payload.get("item") or "").strip()
    if not item or vote not in ("up", "down"):
        raise ValueError("vote/item 无效")
    src = (payload.get("source") or "").strip() or None
    auth = (payload.get("author") or "").strip() or None
    meta: Dict[str, Any] = {"channel": "feishu_ws"}
    if operator:
        for k in ("open_id", "user_id", "union_id", "tenant_key"):
            v = getattr(operator, k, None)
            if v:
                meta[k] = v
    append_feedback(item, vote, source=src, author=auth, meta=meta)


def do_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    def _work() -> None:
        try:
            ev = data.event
            if not ev or not ev.action:
                return
            pl = _parse_action_value(ev.action.value)
            _apply_feedback(pl, ev.operator)
        except Exception as exc:
            LOG.exception("异步写入反馈失败: %s", exc)

    _executor.submit(_work)
    return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已收到反馈，正在写入偏好库…"}})


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        LOG.error("请设置 FEISHU_APP_ID 与 FEISHU_APP_SECRET")
        sys.exit(1)
    handler = (
        EventDispatcherHandler.builder("", "", LogLevel.INFO)
        .register_p2_card_action_trigger(do_card_action_trigger)
        .build()
    )
    cli = Client(app_id, app_secret, LogLevel.INFO, handler)
    LOG.info("飞书长连接（WSS）已启动；处理 card.action.trigger，Ctrl+C 退出")
    cli.start()


_patch_lark_ws_card_callback()

if __name__ == "__main__":
    main()
