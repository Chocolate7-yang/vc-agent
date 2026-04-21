"""feishu_app_send 会话列表分页与去重。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from vc_agent.feishu_app_send import collect_bot_chats


class CollectBotChatsTests(unittest.TestCase):
    @patch("vc_agent.feishu_app_send.list_bot_chats")
    def test_collect_paginates_and_dedupes_chat_id(self, mock_list: MagicMock) -> None:
        mock_list.side_effect = [
            {
                "items": [
                    {"chat_id": "oc_a", "name": "G1"},
                    {"chat_id": "oc_b", "name": "G2"},
                ],
                "has_more": True,
                "page_token": "t1",
            },
            {
                "items": [
                    {"chat_id": "oc_a", "name": "G1-dup"},
                    {"chat_id": "oc_c", "name": "G3"},
                ],
                "has_more": False,
            },
        ]
        out = collect_bot_chats("fake-token", page_size=50)
        self.assertEqual(len(out), 3)
        self.assertEqual([x.get("chat_id") for x in out], ["oc_a", "oc_b", "oc_c"])
        self.assertEqual(out[0].get("name"), "G1")
        mock_list.assert_called()
        self.assertEqual(mock_list.call_count, 2)
        mock_list.assert_any_call("fake-token", page_size=50, page_token=None)
        mock_list.assert_any_call("fake-token", page_size=50, page_token="t1")


if __name__ == "__main__":
    unittest.main()
