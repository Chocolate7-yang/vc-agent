#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from .feishu_app_send import collect_bot_chats, get_tenant_access_token


def _hint_for_feishu_err(msg: str) -> str:
    if "99991672" in msg or "im:chat:readonly" in msg:
        return "提示：缺少群信息权限，请在开放平台申请并发布新版本。"
    if "232025" in msg:
        return "提示：未启用机器人能力，请在开放平台启用并发布。"
    if "232034" in msg:
        return "提示：应用在本租户未安装或未启用。"
    if "232001" in msg:
        return "提示：请求参数有误，请检查 page_size 或接口变更。"
    return ""


def main() -> None:
    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        print("请先设置 FEISHU_APP_ID、FEISHU_APP_SECRET", file=sys.stderr)
        sys.exit(1)
    tok = get_tenant_access_token(app_id, app_secret)
    try:
        items = collect_bot_chats(tok, page_size=100)
    except RuntimeError as e:
        err = str(e)
        print(err, file=sys.stderr)
        hint = _hint_for_feishu_err(err)
        if hint:
            print(hint, file=sys.stderr)
        sys.exit(1)
    if not items:
        print("（无群或未进群：请先把机器人加入群，并检查应用权限）")
        return
    print("chat_id\t群名称")
    for it in items:
        print(f"{it.get('chat_id') or ''}\t{it.get('name') or ''}")


if __name__ == "__main__":
    main()
