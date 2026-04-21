#!/usr/bin/env python3
"""
飞书云文档（docx）：创建文档、Markdown 转块并插入根节点，返回 document_id 与可分享链接。

需在开放平台为应用开启云文档相关权限（至少「创建新版文档」「文本内容转换为云文档块」等），
详见 README 飞书章节。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request

LOG = logging.getLogger("vc_agent.feishu_docx")


def _ssl_ctx():
    import os
    import ssl

    v = (os.getenv("ALLOW_INSECURE_SSL") or "false").lower()
    if v in {"1", "true", "yes", "on"}:
        return ssl._create_unverified_context()
    return None


def _post_json(url: str, token: str, body: Dict[str, Any], *, timeout: int = 120) -> Dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {e.code}: {raw[:2000]}") from e
    out = json.loads(raw)
    if out.get("code") != 0:
        raise RuntimeError(f"飞书 API 错误: {out}")
    return out.get("data") or {}


def _strip_merge_info(obj: Any) -> None:
    """插入文档前需去掉表格块中的 merge_info（只读字段，会导致 1770006）。"""
    if isinstance(obj, dict):
        obj.pop("merge_info", None)
        for v in obj.values():
            _strip_merge_info(v)
    elif isinstance(obj, list):
        for x in obj:
            _strip_merge_info(x)


def _sanitize_descendant_blocks(blocks: List[Dict[str, Any]]) -> None:
    """创建嵌套块前弱化只读/易冲突字段。"""
    for b in blocks:
        if not isinstance(b, dict):
            continue
        b.pop("revision_id", None)
        _strip_merge_info(b)


def create_docx_document(
    tenant_access_token: str,
    *,
    title: str,
    folder_token: Optional[str] = None,
) -> str:
    body: Dict[str, Any] = {"title": (title or "未命名简报")[:800]}
    if folder_token and str(folder_token).strip():
        body["folder_token"] = str(folder_token).strip()
    data = _post_json(
        "https://open.feishu.cn/open-apis/docx/v1/documents",
        tenant_access_token,
        body,
        timeout=45,
    )
    doc = data.get("document") or {}
    doc_id = str(doc.get("document_id") or data.get("document_id") or "").strip()
    if not doc_id:
        raise RuntimeError(f"创建文档无 document_id: {data}")
    return doc_id


def convert_markdown_to_blocks(tenant_access_token: str, markdown: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    data = _post_json(
        "https://open.feishu.cn/open-apis/docx/v1/documents/blocks/convert",
        tenant_access_token,
        {"content_type": "markdown", "content": markdown or " "},
        timeout=120,
    )
    ids = data.get("first_level_block_ids") or []
    blocks = data.get("blocks") or []
    if not isinstance(ids, list) or not ids:
        raise RuntimeError("Markdown 转换未返回 first_level_block_ids")
    if not isinstance(blocks, list) or not blocks:
        raise RuntimeError("Markdown 转换未返回 blocks")
    # 类型收窄
    out_ids = [str(x) for x in ids]
    out_blocks = [x for x in blocks if isinstance(x, dict)]
    return out_ids, out_blocks


def insert_blocks_under_document_root(
    tenant_access_token: str,
    *,
    document_id: str,
    first_level_block_ids: List[str],
    blocks: List[Dict[str, Any]],
) -> None:
    _sanitize_descendant_blocks(blocks)
    url = (
        f"https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}"
        f"/blocks/{document_id}/descendant?document_revision_id=-1"
    )
    body = {
        "index": -1,
        "children_id": first_level_block_ids,
        "descendants": blocks,
        "client_token": str(uuid.uuid4()),
    }
    _post_json(url, tenant_access_token, body, timeout=120)


def build_docx_share_url(document_id: str) -> str:
    """文档链接；默认 feishu.cn，可通过 FEISHU_DOCX_URL_HOST 指定企业域名（如 xxx.feishu.cn）。"""
    import os

    host = (os.getenv("FEISHU_DOCX_URL_HOST") or "feishu.cn").strip().rstrip("/")
    if "://" in host:
        base = host.rstrip("/")
    else:
        base = f"https://{host}"
    return f"{base}/docx/{document_id}"


def create_docx_from_markdown(
    tenant_access_token: str,
    *,
    title: str,
    markdown: str,
    folder_token: Optional[str] = None,
) -> Tuple[str, str]:
    """
    创建空 docx → 转换 Markdown → 插入根节点。
    返回 (document_id, share_url)。
    """
    doc_id = create_docx_document(tenant_access_token, title=title, folder_token=folder_token)
    try:
        level_ids, blocks = convert_markdown_to_blocks(tenant_access_token, markdown)
        if len(blocks) > 1000:
            LOG.warning("转换块数 %s 超过单次插入上限 1000，可能被飞书拒绝", len(blocks))
        insert_blocks_under_document_root(
            tenant_access_token,
            document_id=doc_id,
            first_level_block_ids=level_ids,
            blocks=blocks,
        )
    except Exception as exc:
        LOG.error(
            "云文档已创建 document_id=%s 但写入正文失败，可在云空间打开该文档后手动粘贴 Markdown：%s",
            doc_id,
            exc,
            exc_info=True,
        )
        raise
    return doc_id, build_docx_share_url(doc_id)
