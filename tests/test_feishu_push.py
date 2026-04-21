import json
import unittest
from typing import Any, Dict, List

from vc_agent.feishu_push import build_interactive_message


def _collect_tags(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, dict):
        t = obj.get("tag")
        if isinstance(t, str):
            out.append(t)
        for v in obj.values():
            out.extend(_collect_tags(v))
    elif isinstance(obj, list):
        for x in obj:
            out.extend(_collect_tags(x))
    return out


class FeishuPushCardTests(unittest.TestCase):
    def test_card_with_url_includes_callback_buttons(self) -> None:
        payload: Dict[str, Any] = {
            "date": "2026-04-22",
            "brief_id": "brief_test",
            "insights": ["洞察一"],
            "stats": {
                "monitored_total": 3,
                "passed_count": 1,
                "pref_hint": "提示",
            },
            "sections": [
                {
                    "topic": "AI",
                    "shown": 1,
                    "total": 1,
                    "items": [
                        {
                            "url": "https://www.youtube.com/watch?v=abc",
                            "title": "标题",
                            "content": "正文",
                            "signal": "信号",
                            "source": "YouTube",
                            "author": "alice",
                        }
                    ],
                }
            ],
        }
        msg = build_interactive_message(payload)
        raw = json.dumps(msg, ensure_ascii=False)
        self.assertIn('"tag": "button"', raw)
        self.assertIn("callback", raw)
        tags = _collect_tags(msg)
        self.assertIn("button", tags)
        self.assertIn("collapsible_panel", tags)

    def test_card_without_url_has_no_buttons(self) -> None:
        payload: Dict[str, Any] = {
            "date": "2026-04-22",
            "brief_id": "brief_test2",
            "insights": [],
            "stats": {"monitored_total": 0, "passed_count": 0, "pref_hint": ""},
            "sections": [
                {
                    "topic": "AI",
                    "shown": 1,
                    "total": 1,
                    "items": [
                        {
                            "url": "",
                            "title": "无链接条目",
                            "content": "正文",
                            "signal": "",
                            "source": "",
                            "author": "",
                        }
                    ],
                }
            ],
        }
        msg = build_interactive_message(payload)
        self.assertNotIn("button", _collect_tags(msg))

    def test_item_markdown_includes_full_long_content(self) -> None:
        long_body = "A" * 5000
        payload: Dict[str, Any] = {
            "date": "2026-04-22",
            "brief_id": "brief_long",
            "insights": [],
            "stats": {"monitored_total": 1, "passed_count": 1, "pref_hint": ""},
            "sections": [
                {
                    "topic": "AI",
                    "shown": 1,
                    "total": 1,
                    "items": [
                        {
                            "url": "",
                            "title": "长文",
                            "content": long_body,
                            "signal": "",
                            "source": "",
                            "author": "",
                        }
                    ],
                }
            ],
        }
        msg = build_interactive_message(payload)
        raw = json.dumps(msg, ensure_ascii=False)
        self.assertIn(long_body, raw)


if __name__ == "__main__":
    unittest.main()
