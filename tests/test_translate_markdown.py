from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import translate_markdown as translator  # noqa: E402


class TranslationMockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body)
        self.server.requests.append((self.path, self.headers, payload))
        source = payload["messages"][1]["content"]
        def translate(value):
            return (
                value.replace("Hello", "你好")
                .replace("This is text", "这是一段文字")
                .replace("token", "标记")
                .replace("Figure caption", "图注")
                .replace("Table caption", "表题")
                .replace("APPENDIX", "附录")
            )

        translated = translate(source)
        threshold = getattr(self.server, "corrupt_over_chars", None)
        if threshold is not None and len(source) > threshold:
            placeholder = re.search(r"__MDT_[A-F0-9X]+_\d{5}__", translated)
            if placeholder:
                translated = translated.replace(placeholder.group(0), "missing", 1)
        response = json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": translated}}],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format, *_args):
        return


class MockServer:
    def __init__(self, *, corrupt_over_chars=None):
        self.corrupt_over_chars = corrupt_over_chars

    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TranslationMockHandler)
        self.server.requests = []
        self.server.corrupt_over_chars = self.corrupt_over_chars
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class MarkdownTranslationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source_dir = self.root / "paper-mineru"
        (self.source_dir / "images").mkdir(parents=True)
        (self.source_dir / "images" / "figure.png").write_bytes(b"image")
        (self.source_dir / "images" / "table.png").write_bytes(b"table-image")
        self.table_body = "<table><tr><td>TableCell</td><td>42</td></tr></table>"
        self.source = self.source_dir / "full.md"
        self.source.write_text(
            f"""# Hello

This is text with token, $E = mc^2$, `code`, and [site](https://example.com/a).

![](images/figure.png)
Figure caption

Table caption
{self.table_body}

## REFERENCES

Smith, A. Hello paper. 2025.

## APPENDIX

Hello again.
""",
            encoding="utf-8",
        )
        self.content_list = self.source_dir / "test_content_list.json"
        self.content_list.write_text(
            json.dumps(
                [
                    {
                        "type": "table",
                        "img_path": "images/table.png",
                        "table_body": self.table_body,
                    }
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def run_main(self, arguments, env=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = translator.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_translation_preserves_structure_and_shares_source_assets(self):
        secret = "sk-test-secret-that-must-not-leak"
        output = self.source_dir / "translation-zh-CN"
        with MockServer() as server:
            arguments = [
                str(self.source),
                "--base-url",
                server.base_url,
                "--model",
                "ds-v4-flash",
                "--max-chars",
                "1000",
                "--yes",
                "-o",
                str(output),
            ]
            code, stdout, stderr = self.run_main(
                arguments, {"MARKDOWN_TRANSLATION_API_KEY": secret}
            )
            self.assertEqual(code, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["model"], "deepseek-v4-flash")
            translated = Path(result["markdown_path"]).read_text(encoding="utf-8")
            self.assertIn("# 你好", translated)
            self.assertIn("$E = mc^2$", translated)
            self.assertIn("with token", translated)
            self.assertNotIn("标记", translated)
            self.assertIn("[site](https://example.com/a)", translated)
            self.assertIn("![](../images/figure.png)", translated)
            self.assertIn("![](../images/table.png)", translated)
            self.assertIn("表题", translated)
            self.assertNotIn("<table", translated)
            self.assertIn("## 参考文献", translated)
            self.assertIn("Smith, A. Hello paper. 2025.", translated)
            self.assertIn("## 附录", translated)
            self.assertFalse((output / "images").exists())
            self.assertEqual(result["asset_mode"], "shared")
            self.assertEqual(result["copied_assets"], 0)
            self.assertEqual(result["shared_assets"], 2)
            self.assertEqual(result["table_mode"], "image")
            self.assertEqual(result["table_images"], 1)
            self.assertEqual(
                Path(result["content_list_path"]), self.content_list.resolve()
            )
            self.assertTrue((output / "../images/figure.png").resolve().is_file())
            self.assertTrue((output / "../images/table.png").resolve().is_file())
            state_text = (output / ".translation-state.json").read_text(encoding="utf-8")
            self.assertNotIn(secret, stdout + stderr + state_text)
            self.assertTrue(server.server.requests)
            self.assertTrue(
                all(
                    request[2].get("thinking") == {"type": "disabled"}
                    for request in server.server.requests
                )
            )
            self.assertTrue(
                all(
                    request[1].get("Authorization") == f"Bearer {secret}"
                    for request in server.server.requests
                )
            )
            self.assertTrue(
                all(
                    "TableCell" not in request[2]["messages"][1]["content"]
                    for request in server.server.requests
                )
            )
            self.assertTrue(
                all(
                    "Do-not-translate terms:" in request[2]["messages"][0]["content"]
                    and "`token`" in request[2]["messages"][0]["content"]
                    for request in server.server.requests
                )
            )

            first_request_count = len(server.server.requests)
            code, stdout, stderr = self.run_main(
                arguments, {"MARKDOWN_TRANSLATION_API_KEY": secret}
            )
            self.assertEqual(code, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["api_calls"], 0)
            self.assertGreater(result["cache_hits"], 0)
            self.assertEqual(len(server.server.requests), first_request_count)

    def test_copy_assets_keeps_translation_directory_self_contained(self):
        output = self.source_dir / "translation-copy"
        with MockServer() as server:
            code, stdout, stderr = self.run_main(
                [
                    str(self.source),
                    "--base-url",
                    server.base_url,
                    "--model",
                    "ds-v4-flash",
                    "--copy-assets",
                    "--yes",
                    "-o",
                    str(output),
                ],
                {"MARKDOWN_TRANSLATION_API_KEY": "sk-test-secret-copy-assets"},
            )
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        translated = Path(result["markdown_path"]).read_text(encoding="utf-8")
        self.assertIn("![](images/figure.png)", translated)
        self.assertIn("![](images/table.png)", translated)
        self.assertTrue((output / "images" / "figure.png").is_file())
        self.assertTrue((output / "images" / "table.png").is_file())
        self.assertEqual(result["asset_mode"], "copied")
        self.assertEqual(result["copied_assets"], 2)
        self.assertEqual(result["shared_assets"], 0)

    def test_table_mode_html_keeps_searchable_table(self):
        conversion = translator.replace_html_tables_with_images(
            self.source.read_text(encoding="utf-8"),
            self.source,
            table_mode="html",
            content_list_path=None,
        )
        self.assertEqual(conversion.mode, "html")
        self.assertEqual(conversion.image_count, 0)
        self.assertIn(self.table_body, conversion.markdown)

    def test_auto_table_mode_falls_back_without_content_list(self):
        isolated = self.root / "isolated"
        isolated.mkdir()
        source = isolated / "paper.md"
        source.write_text(self.table_body, encoding="utf-8")
        conversion = translator.replace_html_tables_with_images(
            self.table_body,
            source,
            table_mode="auto",
            content_list_path=None,
        )
        self.assertEqual(conversion.mode, "html")
        self.assertEqual(conversion.markdown, self.table_body)

    def test_forced_image_mode_rejects_missing_table_image(self):
        missing = self.source_dir / "missing_content_list.json"
        missing.write_text(
            json.dumps(
                [
                    {
                        "type": "table",
                        "img_path": "images/missing.png",
                        "table_body": self.table_body,
                    }
                ]
            ),
            encoding="utf-8",
        )
        with self.assertRaises(translator.TranslationError):
            translator.replace_html_tables_with_images(
                self.table_body,
                self.source,
                table_mode="image",
                content_list_path=missing,
            )

    def test_shared_assets_support_custom_output_and_html_images(self):
        output_path = self.root / "custom" / "nested" / "paper-CN.md"
        source_markdown = (
            "![](images/figure.png)\n"
            '<img class="figure" src="images/figure.png" alt="figure">\n'
        )
        rewritten, count = translator.rewrite_shared_asset_paths(
            source_markdown, self.source, output_path
        )
        expected = "../../paper-mineru/images/figure.png"
        self.assertIn(f"![]({expected})", rewritten)
        self.assertIn(f'src="{expected}"', rewritten)
        self.assertEqual(count, 1)

    def test_noninteractive_translation_requires_yes(self):
        output = self.source_dir / "blocked"
        code, _stdout, stderr = self.run_main([str(self.source), "-o", str(output)])
        self.assertEqual(code, 2)
        self.assertIn("--yes", stderr)
        self.assertFalse((output / "full-CN.md").exists())

    def test_api_key_file_may_be_markdown(self):
        key_file = self.root / "DeepSeek-API.md"
        key_file.write_text("DeepSeek API\n\nsk-example-secret-123456789\n", encoding="utf-8")
        self.assertEqual(
            translator.read_api_key_file(key_file), "sk-example-secret-123456789"
        )

    def test_do_not_translate_markdown_list_is_loaded_and_protected(self):
        word_list = self.root / "words.md"
        word_list.write_text(
            "# 不翻译词汇表\n\n- `token`\n- EarthGPT\n- `token`\n",
            encoding="utf-8",
        )
        prompt_text, terms, digest = translator.load_do_not_translate(word_list)
        self.assertEqual(terms, ("token", "EarthGPT"))
        self.assertEqual(prompt_text, "- `token`\n- `EarthGPT`")
        self.assertTrue(digest)

        source = "Use token, tokens, and EarthGPT."
        protected = translator.protect_markdown(source, terms)
        self.assertNotIn(" token,", protected.text)
        self.assertIn("tokens", protected.text)
        self.assertEqual(translator.restore_markdown(protected.text, protected), source)

    def test_formula_placeholders_may_reorder_but_images_may_not(self):
        formulas = translator.protect_markdown("Action $a_t$, state $s_t$.")
        first, second = [key for key, _value in formulas.replacements]
        reordered = formulas.text.replace(first, "TEMP", 1).replace(second, first, 1).replace(
            "TEMP", second, 1
        )
        restored = translator.restore_markdown(reordered, formulas)
        self.assertIn("$s_t$", restored)
        self.assertIn("$a_t$", restored)

        images = translator.protect_markdown("![](images/a.png)\n![](images/b.png)\n")
        first, second = [key for key, _value in images.replacements]
        reordered = images.text.replace(first, "TEMP", 1).replace(second, first, 1).replace(
            "TEMP", second, 1
        )
        with self.assertRaises(translator.TranslationError):
            translator.restore_markdown(reordered, images)

    def test_final_validation_allows_formula_order_but_not_image_order(self):
        translator.validate_final_document("$a$ then $b$", "$b$ 然后 $a$")
        with self.assertRaises(translator.TranslationError):
            translator.validate_final_document(
                "![](images/a.png)\n![](images/b.png)",
                "![](images/b.png)\n![](images/a.png)",
            )

    def test_failed_chunk_is_split_automatically_and_parent_is_cached(self):
        source = self.source_dir / "adaptive.md"
        source.write_text(
            "# Hello\n\n"
            + ("This is text with $x$ and enough words to split safely. " * 45)
            + "\n",
            encoding="utf-8",
        )
        output = self.source_dir / "adaptive-zh"
        secret = "sk-test-adaptive-split"
        with MockServer(corrupt_over_chars=900) as server:
            arguments = [
                str(source),
                "--base-url",
                server.base_url,
                "--model",
                "ds-v4-flash",
                "--max-chars",
                "5000",
                "--adaptive-min-chars",
                "200",
                "--yes",
                "-o",
                str(output),
            ]
            code, stdout, stderr = self.run_main(
                arguments, {"MARKDOWN_TRANSLATION_API_KEY": secret}
            )
            self.assertEqual(code, 0, stderr)
            result = json.loads(stdout)
            self.assertGreater(result["adaptive_splits"], 0)
            first_request_count = len(server.server.requests)

            code, stdout, stderr = self.run_main(
                arguments, {"MARKDOWN_TRANSLATION_API_KEY": secret}
            )
            self.assertEqual(code, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["api_calls"], 0)
            self.assertEqual(len(server.server.requests), first_request_count)

    def test_accidental_code_span_is_removed_from_plain_protected_term(self):
        translated = translator.normalize_accidental_term_code_spans(
            "A token.",
            "一个 `token`。",
            ["token"],
        )
        self.assertEqual(translated, "一个 token。")

    def test_preserved_url_is_separated_from_translated_text(self):
        source = "Code: https://example.com/project."
        translated = "代码见 https://example.com/project.获取。"
        self.assertEqual(
            translator.normalize_preserved_url_boundaries(source, translated),
            "代码见 https://example.com/project. 获取。",
        )

    def test_changed_or_missing_placeholder_is_rejected(self):
        protected = translator.protect_markdown("Text $x+y$ and `code`")
        damaged = protected.text.replace(protected.replacements[0][0], "missing", 1)
        with self.assertRaises(translator.TranslationError):
            translator.restore_markdown(damaged, protected)

    def test_final_document_validation_catches_joined_heading(self):
        source = "Paragraph.\n\n## REFERENCES\n"
        damaged = "段落。## 参考文献\n"
        with self.assertRaises(translator.TranslationError):
            translator.validate_final_document(source, damaged)

    def test_remote_http_base_url_is_rejected(self):
        with self.assertRaises(translator.TranslationError):
            translator.normalize_base_url("http://example.com/v1")

    def test_html_table_body_is_preserved_while_caption_is_translatable(self):
        source = "Table caption\n<table><tr><td>English</td></tr></table>\n\n"
        parts = translator.document_parts(
            source,
            target_language="zh-CN",
            translate_references=False,
        )
        self.assertEqual("".join(part.text for part in parts), source)
        table_parts = [part for part in parts if "<table" in part.text]
        self.assertEqual(len(table_parts), 1)
        self.assertFalse(table_parts[0].translatable)
        self.assertTrue(any("Table caption" in part.text and part.translatable for part in parts))

    def test_html_caption_inside_table_is_translatable(self):
        source = (
            "<table><caption>Table caption</caption>"
            "<tr><td>English</td></tr></table>\n\n"
        )
        parts = translator.document_parts(
            source,
            target_language="zh-CN",
            translate_references=False,
        )
        self.assertEqual("".join(part.text for part in parts), source)
        self.assertTrue(any(part.text == "Table caption" and part.translatable for part in parts))
        self.assertTrue(any("<td>English</td>" in part.text and not part.translatable for part in parts))

    def test_markdown_table_body_is_preserved_while_caption_is_translatable(self):
        source = (
            "Table caption\n"
            "| Method | Score |\n"
            "| --- | ---: |\n"
            "| Ours | 99.0 |\n\n"
        )
        parts = translator.document_parts(
            source,
            target_language="zh-CN",
            translate_references=False,
        )
        self.assertEqual("".join(part.text for part in parts), source)
        table_parts = [part for part in parts if "| Method | Score |" in part.text]
        self.assertEqual(len(table_parts), 1)
        self.assertFalse(table_parts[0].translatable)
        self.assertTrue(any("Table caption" in part.text and part.translatable for part in parts))

    def test_chunk_boundary_whitespace_is_restored(self):
        source = "Paragraph.\n\n"
        self.assertEqual(
            translator.preserve_outer_whitespace(source, "Translated paragraph."),
            "Translated paragraph.\n\n",
        )

if __name__ == "__main__":
    unittest.main()
