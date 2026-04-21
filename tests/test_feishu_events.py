'''
测试飞书事件回调的 payload 处理
'''
import json
import unittest

from vc_agent.feishu_events import _normalize_ws_payload_for_card_action


class FeishuEventsPayloadTests(unittest.TestCase):
    def test_coerces_string_action_value_to_dict(self) -> None:
        inner = {"vote": "up", "item": "https://example.com/x", "source": "s", "author": "a"}
        envelope = {
            "schema": "2.0",
            "header": {"event_type": "card.action.trigger"},
            "event": {"action": {"tag": "button", "value": json.dumps(inner, ensure_ascii=False)}},
        }
        raw = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        out = _normalize_ws_payload_for_card_action(raw)
        data = json.loads(out.decode("utf-8"))
        self.assertIsInstance(data["event"]["action"]["value"], dict)
        self.assertEqual(data["event"]["action"]["value"]["vote"], "up")
        self.assertEqual(data["event"]["action"]["value"]["item"], "https://example.com/x")

    def test_non_json_bytes_unchanged(self) -> None:
        b = b"not json"
        self.assertEqual(_normalize_ws_payload_for_card_action(b), b)


if __name__ == "__main__":
    unittest.main()
