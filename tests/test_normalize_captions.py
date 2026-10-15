from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from normalize_captions import normalize  # noqa: E402


class NormalizeCaptionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md = Path(self.tmp.name) / "full-CN.md"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str) -> None:
        self.md.write_text(text, encoding="utf-8")

    def read(self) -> str:
        return self.md.read_text(encoding="utf-8")

    def test_caption_after_an_image_gains_the_colon(self):
        self.write("![](a.jpg)\n图 5. 边界框分布。\n")
        self.assertEqual(normalize(self.md, verbose=False), 1)
        self.assertEqual(self.read(), "![](a.jpg)\n图5：边界框分布。\n")

    def test_space_form_needs_a_panel_label_or_image_above(self):
        self.write("![](a.jpg)\n(a)\n\n表 2 数据集统计。\n")
        self.assertEqual(normalize(self.md, verbose=False), 1)
        self.assertEqual(self.read(), "![](a.jpg)\n(a)\n\n表2：数据集统计。\n")

    def test_body_sentence_that_opens_with_a_table_number_keeps_its_spacing(self):
        self.write("正文段落。\n\n表 3 展示了语义元素平均数量。\n")
        self.assertEqual(normalize(self.md, verbose=False), 0)
        self.assertEqual(self.read(), "正文段落。\n\n表 3 展示了语义元素平均数量。\n")

    def test_panel_label_may_carry_text_after_the_bracket(self):
        self.write("![](a.jpg)\n(c) UpperNet在ADE20K上。\n图6. 训练效率。\n")
        self.assertEqual(normalize(self.md, verbose=False), 1)
        self.assertEqual(self.read(), "![](a.jpg)\n(c) UpperNet在ADE20K上。\n图6：训练效率。\n")

    def test_a_normalized_file_is_left_alone(self):
        self.write("![](a.jpg)\n图5：边界框分布。\n\n表 3 展示了语义元素。\n")
        before = self.read()
        self.assertEqual(normalize(self.md, verbose=False), 0)
        self.assertEqual(self.read(), before)


if __name__ == "__main__":
    unittest.main()
