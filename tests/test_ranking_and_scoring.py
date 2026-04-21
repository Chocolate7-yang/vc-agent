import unittest

from vc_agent.agent import RawItem, classify_and_score, deduplicate


class RankingScoringTests(unittest.TestCase):
    def test_classify_and_score_has_reason_factors(self) -> None:
        item = RawItem(
            source="YouTube",
            title="LLM startup wins major enterprise orders",
            author="demo",
            published="",
            link="https://example.com/a",
            summary="The company announced mass production and large customer contracts.",
            channel_id=None,
        )
        scored = classify_and_score(item, prefs={"sources": {}, "authors": {}, "link_multiplier": {}, "domains": {}})
        self.assertIsNotNone(scored)
        assert scored is not None
        self.assertIn("biz=", scored.reason)
        self.assertIn("risk=", scored.reason)

    def test_deduplicate_keep_high_score(self) -> None:
        i1 = RawItem("YouTube", "LLM Same Title", "a", "", "https://a", "x", None)
        i2 = RawItem("YouTube", "LLM Same Title", "b", "", "https://b", "x", None)
        s1 = classify_and_score(i1, prefs={"sources": {}, "authors": {}, "link_multiplier": {}, "domains": {}})
        s2 = classify_and_score(i2, prefs={"sources": {}, "authors": {}, "link_multiplier": {}, "domains": {}})
        assert s1 is not None and s2 is not None
        s1.score = 0.2
        s2.score = 0.8
        out = deduplicate([s1, s2])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].raw.link, "https://b")


if __name__ == "__main__":
    unittest.main()
