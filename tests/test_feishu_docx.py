"""飞书云文档辅助逻辑单测（无网络）。"""
import unittest

from vc_agent.feishu_docx import _sanitize_descendant_blocks, build_docx_share_url


class FeishuDocxUtilTests(unittest.TestCase):
    def test_strip_merge_info_from_blocks(self) -> None:
        blocks = [
            {
                "block_id": "t1",
                "block_type": 31,
                "table": {"property": {"row_size": 1, "column_size": 2}, "merge_info": [{"r": 1}]},
                "children": [],
            }
        ]
        _sanitize_descendant_blocks(blocks)
        tbl = blocks[0].get("table")
        self.assertIsInstance(tbl, dict)
        self.assertNotIn("merge_info", tbl)

    def test_build_docx_share_url_default_host(self) -> None:
        import os

        os.environ.pop("FEISHU_DOCX_URL_HOST", None)
        self.assertEqual(build_docx_share_url("doxabc123"), "https://feishu.cn/docx/doxabc123")

    def test_build_docx_share_url_custom_host(self) -> None:
        import os

        os.environ["FEISHU_DOCX_URL_HOST"] = "acme.feishu.cn"
        try:
            self.assertEqual(build_docx_share_url("doxx"), "https://acme.feishu.cn/docx/doxx")
        finally:
            os.environ.pop("FEISHU_DOCX_URL_HOST", None)


if __name__ == "__main__":
    unittest.main()
