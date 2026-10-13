from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from merge_figure_panels import find_groups, rewrite_markdown  # noqa: E402


CAPTION_KEY = {"chart": "chart_caption", "image": "image_caption", "table": "table_caption"}


def panel(page: int, y0: int, y1: int, name: str, captions=None, kind="chart") -> dict:
    return {
        "type": kind,
        "page_idx": str(page),
        "bbox": [100, y0, 500, y1],
        "img_path": f"images/{name}",
        CAPTION_KEY.get(kind, "chart_caption"): json.dumps(captions or []),
    }


class FindGroupsTests(unittest.TestCase):
    def test_table_above_figure_is_not_absorbed(self):
        """A table image sitting above a figure must stay out of the group."""
        items = [
            {"type": "text", "page_idx": "3", "bbox": [0, 0, 10, 10], "text": "Universality grid."},
            panel(3, 100, 200, "table-image.jpg", kind="table"),
            panel(3, 300, 420, "panel-a.jpg"),
            panel(3, 430, 560, "panel-b.jpg", ["Figure 16. The training-free read reproduces the encoder."]),
        ]
        groups = find_groups(items)
        self.assertEqual([g["figure"] for g in groups], [16])
        self.assertEqual([p["img_path"] for p in groups[0]["panels"]],
                         ["images/panel-a.jpg", "images/panel-b.jpg"])

    def test_colon_caption_from_vlm_output_closes_the_run(self):
        """VLM output writes `Figure N: ...`; the period-only pattern missed every figure."""
        items = [
            panel(7, 88, 209, "panel-a.jpg"),
            panel(7, 88, 209, "panel-b.jpg"),
            panel(7, 88, 210, "panel-c.jpg", ["Figure 4: Quality ratings and evasion."]),
        ]
        groups = find_groups(items)
        self.assertEqual([g["figure"] for g in groups], [4])
        self.assertEqual(len(groups[0]["panels"]), 3)

    def test_table_typed_panel_carrying_figure_caption_joins_the_run(self):
        """MinerU sometimes types one panel as `table` while keeping the figure caption."""
        items = [
            {"type": "text", "page_idx": "7", "bbox": [0, 0, 10, 10], "text": "prose"},
            panel(7, 378, 585, "upper.jpg"),
            panel(7, 589, 705, "lower.jpg",
                  ["Figure 5: Craft rating and evasion by model."], kind="table"),
        ]
        groups = find_groups(items)
        self.assertEqual([g["figure"] for g in groups], [5])
        self.assertEqual([p["img_path"] for p in groups[0]["panels"]],
                         ["images/upper.jpg", "images/lower.jpg"])

    def test_image_type_panel_is_read_from_image_caption(self):
        """`image` blocks carry `image_caption`, not `chart_caption`."""
        items = [
            panel(9, 300, 400, "a.jpg", kind="image"),
            panel(9, 410, 520, "b.jpg", ["Figure 21: Two views."], kind="image"),
        ]
        groups = find_groups(items)
        self.assertEqual([g["figure"] for g in groups], [21])

    def test_panel_label_on_first_panel_does_not_close_the_run(self):
        items = [
            panel(4, 300, 420, "panel-a.jpg", ["(a) scaling on the face pool"]),
            panel(4, 430, 560, "panel-b.jpg",
                  ["(b) codebook vs. continuous attention",
                   "Figure 17. Left: under the paired protocol ..."]),
        ]
        groups = find_groups(items)
        self.assertEqual([g["figure"] for g in groups], [17])
        self.assertEqual(len(groups[0]["panels"]), 2)

    def test_single_panel_figure_is_not_a_group(self):
        items = [
            {"type": "text", "page_idx": "5", "bbox": [0, 0, 10, 10], "text": "prose"},
            panel(5, 300, 500, "one-panel.jpg", ["Figure 10. Recognition performance."]),
        ]
        self.assertEqual(find_groups(items), [])

    def test_run_does_not_cross_pages(self):
        items = [
            panel(6, 300, 400, "panel-a.jpg"),
            panel(7, 100, 200, "panel-b.jpg", ["Figure 18. Two pages."]),
        ]
        self.assertEqual(find_groups(items), [])


class RewriteMarkdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md = Path(self.tmp.name) / "full.md"
        self.group = {"figure": 12,
                      "panels": [panel(2, 300, 400, "aaaa.jpg"),
                                 panel(2, 410, 500, "bbbb.jpg"),
                                 panel(2, 510, 600, "cccc.jpg",
                                       ["Figure 12. One recall, three views."])]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_panels_collapse_to_one_ref_and_keep_panel_labels(self):
        self.md.write_text(
            "Before.\n\n"
            "![](images/aaaa.jpg)\n\n"
            "![](images/bbbb.jpg)\n"
            "(b) codebook vs. continuous attention\n\n"
            "![](images/cccc.jpg)  \n"
            "Figure 12. One recall, three views.\n\n"
            "After.\n",
            encoding="utf-8")
        status = rewrite_markdown(self.md, self.group, "figure-12.jpg", "")
        self.assertIn("merged 3 panels", status)
        text = self.md.read_text(encoding="utf-8")
        self.assertIn("![](images/figure-12.jpg)\n", text)
        self.assertNotIn("aaaa.jpg", text)
        self.assertNotIn("cccc.jpg", text)
        self.assertIn("(b) codebook vs. continuous attention\n", text)
        self.assertIn("Figure 12. One recall, three views.", text)
        self.assertEqual(text.count("figure-12.jpg"), 1)

    def test_rerun_is_idempotent(self):
        self.md.write_text(
            "![](images/aaaa.jpg)\n\n![](images/bbbb.jpg)\n![](images/cccc.jpg)\n",
            encoding="utf-8")
        rewrite_markdown(self.md, self.group, "figure-12.jpg", "")
        first = self.md.read_text(encoding="utf-8")
        status = rewrite_markdown(self.md, self.group, "figure-12.jpg", "")
        self.assertIn("skip", status)
        self.assertEqual(first, self.md.read_text(encoding="utf-8"))

    def test_prose_between_panels_is_refused(self):
        self.md.write_text(
            "![](images/aaaa.jpg)\n\n"
            "Some unrelated sentence.\n\n"
            "![](images/bbbb.jpg)\n![](images/cccc.jpg)\n",
            encoding="utf-8")
        before = self.md.read_text(encoding="utf-8")
        status = rewrite_markdown(self.md, self.group, "figure-12.jpg", "")
        self.assertIn("unexpected content", status)
        self.assertEqual(before, self.md.read_text(encoding="utf-8"))

    def test_translation_prefix_is_shared_images_dir(self):
        self.md.write_text(
            "![](../images/aaaa.jpg)\n\n![](../images/bbbb.jpg)\n![](../images/cccc.jpg)\n",
            encoding="utf-8")
        rewrite_markdown(self.md, self.group, "figure-12.jpg", "../")
        self.assertIn("![](../images/figure-12.jpg)\n", self.md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
