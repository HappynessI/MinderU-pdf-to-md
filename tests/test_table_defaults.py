from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mineru_pdf_to_md as converter
import translate_markdown as translator

class TableDefaultsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(converter.build_parser().parse_args(["a.pdf"]).table_mode, "image")
        self.assertEqual(translator.build_parser().parse_args(["a.md"]).table_mode, "image")

    def test_image_conversion_and_missing_crop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "full.md"
            table = "<table><caption>Results</caption><tr><td>42</td></tr></table>"
            source.write_text("# Paper\n\n" + table)
            (root / "table.png").write_bytes(b"fixture")
            (root / "x_content_list.json").write_text(json.dumps([
                {"type": "table", "table_body": table, "img_path": "table.png"}
            ]))
            result = translator.replace_html_tables_with_images(
                source.read_text(), source, table_mode="image", content_list_path=None)
            self.assertIn("![](table.png)", result.markdown)
            self.assertIn("Results", result.markdown)
            self.assertNotIn("<table", result.markdown)
            (root / "table.png").unlink()
            with self.assertRaises(translator.TranslationError):
                translator.replace_html_tables_with_images(
                    source.read_text(), source, table_mode="image", content_list_path=None)

    def test_plain_markdown_table_cannot_silently_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(translator.TranslationError):
                translator.replace_html_tables_with_images(
                    "| a | b |\n| --- | --- |\n| 1 | 2 |\n",
                    Path(folder) / "full.md", table_mode="image", content_list_path=None)

    def test_existing_images_are_idempotent(self):
        text = "# Paper\n\n![](table.png)\n\nCaption"
        result = translator.replace_html_tables_with_images(
            text, Path("full.md"), table_mode="image", content_list_path=None)
        self.assertEqual(result.markdown, text)

    def test_pdf_main_replaces_tables_before_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "source.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            output = root / "out"
            def fake_convert(args, source, destination, token):
                table = "<table><tr><td>42</td></tr></table>"
                md = destination / "full.md"
                md.write_text(table)
                (destination / "table.png").write_bytes(b"fixture")
                (destination / "x_content_list.json").write_text(json.dumps([
                    {"type": "table", "table_body": table, "img_path": "table.png"}
                ]))
                return {"markdown_path": str(md)}
            with mock.patch.object(converter, "resolve_token", return_value="fixture"), mock.patch.object(converter, "convert_precise", side_effect=fake_convert):
                self.assertEqual(converter.main([str(pdf), "-o", str(output), "--yes"]), 0)
            self.assertIn("![](table.png)", (output / "full.md").read_text())

if __name__ == "__main__":
    unittest.main()
