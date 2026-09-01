from __future__ import annotations

import contextlib
import io
import json
import os
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
                .replace("Figure caption", "图注")
                .replace("Table caption", "表题")
                .replace("APPENDIX", "附录")
            )

        translated = translate(source)
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
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TranslationMockHandler)
        self.server.requests = []
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
        self.source = self.source_dir / "full.md"
        self.source.write_text(
            """# Hello

This is text with $E = mc^2$, `code`, and [site](https://example.com/a).

![](images/figure.png)
Figure caption

Table caption
<table><tr><td>TableCell</td><td>42</td></tr></table>

## REFERENCES

Smith, A. Hello paper. 2025.

## APPENDIX

Hello again.
""",
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

    def test_translation_preserves_structure_skips_references_and_copies_assets(self):
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
            self.assertIn("[site](https://example.com/a)", translated)
            self.assertIn("![](images/figure.png)", translated)
            self.assertIn("表题", translated)
            self.assertIn("<table><tr><td>TableCell</td><td>42</td></tr></table>", translated)
            self.assertIn("## 参考文献", translated)
            self.assertIn("Smith, A. Hello paper. 2025.", translated)
            self.assertIn("## 附录", translated)
            self.assertTrue((output / "images" / "figure.png").is_file())
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

            first_request_count = len(server.server.requests)
            code, stdout, stderr = self.run_main(
                arguments, {"MARKDOWN_TRANSLATION_API_KEY": secret}
            )
            self.assertEqual(code, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["api_calls"], 0)
            self.assertGreater(result["cache_hits"], 0)
            self.assertEqual(len(server.server.requests), first_request_count)

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
