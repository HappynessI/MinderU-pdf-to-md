from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from markdown_postprocess import normalize_algorithm_blocks  # noqa: E402


class MarkdownPostprocessTests(unittest.TestCase):
    def test_algorithm_div_becomes_clean_fenced_pseudocode(self):
        source = """Before.

<div style="white-space: pre-wrap" class="mineru-algorithm extra">
Algorithm 1 Trajectory Decoding
Input: hidden state of the &lt; trajectory &gt; token $h_{tra}$
1: $h_{0} \\leftarrow \\text{Project}(h_{tra})$
2: while $t \\leq T \\&amp;\\&amp; P_{flag}$ do
3: $S \\leftarrow S \\cup \\{S_t\\}$
4: end while
</div>

After.
"""
        converted, count = normalize_algorithm_blocks(source)
        self.assertEqual(count, 1)
        self.assertIn("**Algorithm 1 Trajectory Decoding**", converted)
        self.assertIn("```text\n", converted)
        self.assertIn("Input: hidden state of the <trajectory> token h_tra", converted)
        self.assertIn("1: h_0 ← Project(h_tra)", converted)
        self.assertIn("2: while t ≤ T && P_flag do", converted)
        self.assertIn("3:     S ← S ∪ {S_t}", converted)
        self.assertIn("4: end while", converted)
        self.assertNotIn("mineru-algorithm", converted)
        self.assertNotIn("&lt;", converted)
        self.assertNotIn("$", converted)
        self.assertNotIn("\\leftarrow", converted)

    def test_regular_div_is_unchanged(self):
        source = '<div class="note">x &lt; y and $z$</div>'
        converted, count = normalize_algorithm_blocks(source)
        self.assertEqual(count, 0)
        self.assertEqual(converted, source)

    def test_normalization_is_idempotent(self):
        source = """<div class="mineru-algorithm">
Algorithm 2 Example
1: $x \\geq y$
</div>"""
        converted, first_count = normalize_algorithm_blocks(source)
        second, second_count = normalize_algorithm_blocks(converted)
        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(second, converted)

    def test_nested_latex_formatting_is_flattened(self):
        source = """<div class="mineru-algorithm">
Algorithm 3 Example
1: $F^{T_1} \\Leftarrow \\text{Interpolate}(\\text{DINOv3}(I^{T_1}), (H, W))$
2: $p \\leftarrow \\frac{1}{N} \\sum_{i=1}^{N} x_i$
</div>"""
        converted, count = normalize_algorithm_blocks(source)
        self.assertEqual(count, 1)
        self.assertIn("F^(T_1) ⇐ Interpolate(DINOv3(I^(T_1)), (H, W))", converted)
        self.assertIn("p ← (1) / (N) Σ_(i=1)^N x_i", converted)
        self.assertNotRegex(converted, r"\\[A-Za-z]+")


if __name__ == "__main__":
    unittest.main()
