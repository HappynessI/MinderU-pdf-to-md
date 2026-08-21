from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mineru_pdf_to_md as client  # noqa: E402


PDF_BYTES = b"%PDF-1.4\n% MinerU integration fixture\n%%EOF\n"


def make_result_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("result/full.md", "# Precise result\n\nConverted by mock MinerU.\n")
        archive.writestr("result/images/figure.txt", "image-placeholder")
    return buffer.getvalue()


class MinerUMockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, body, content_type="application/octet-stream"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def root_url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append(("POST", self.path, self.headers, body))
        if self.path == "/api/v1/agent/parse/file":
            self._json(
                {
                    "code": 0,
                    "data": {
                        "task_id": "agent-task",
                        "file_url": self.root_url + "/upload/agent",
                    },
                    "msg": "ok",
                }
            )
            return
        if self.path == "/api/v4/file-urls/batch":
            self._json(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": [self.root_url + "/upload/precise"],
                    },
                    "msg": "ok",
                }
            )
            return
        self._json({"code": -1, "msg": "unknown path"}, status=404)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append(("PUT", self.path, self.headers, body))
        self._bytes(b"")

    def do_GET(self):
        self.server.requests.append(("GET", self.path, self.headers, b""))
        if self.path == "/api/v1/agent/parse/agent-task":
            self._json(
                {
                    "code": 0,
                    "data": {
                        "task_id": "agent-task",
                        "state": "done",
                        "markdown_url": self.root_url + "/download/full.md",
                    },
                    "msg": "ok",
                }
            )
            return
        if self.path == "/api/v4/extract-results/batch/batch-1":
            self._json(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "extract_result": [
                            {
                                "file_name": "sample.pdf",
                                "state": "done",
                                "full_zip_url": self.root_url + "/download/result.zip",
                            }
                        ],
                    },
                    "msg": "ok",
                }
            )
            return
        if self.path == "/download/full.md":
            self._bytes(b"# Agent result\n\nConverted by mock MinerU.\n", "text/markdown")
            return
        if self.path == "/download/result.zip":
            self._bytes(make_result_zip(), "application/zip")
            return
        self._json({"code": -1, "msg": "unknown path"}, status=404)

    def log_message(self, _format, *_args):
        return


class MockServer:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MinerUMockHandler)
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


class MinerUClientTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.pdf = self.root / "sample.pdf"
        self.pdf.write_bytes(PDF_BYTES)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_main(self, arguments, env=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = client.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_agent_mode_uploads_and_downloads_markdown(self):
        with MockServer() as mock_server:
            output = self.root / "agent-output"
            code, stdout, stderr = self.run_main(
                [
                    str(self.pdf),
                    "--mode",
                    "agent",
                    "--base-url",
                    mock_server.base_url,
                    "--poll-interval",
                    "0.01",
                    "--yes",
                    "-o",
                    str(output),
                ]
            )
            self.assertEqual(code, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["mode"], "agent")
            self.assertIn("Agent result", Path(result["markdown_path"]).read_text())
            uploads = [request for request in mock_server.server.requests if request[0] == "PUT"]
            self.assertEqual(uploads[0][3], PDF_BYTES)

    def test_precise_mode_sends_token_only_to_api_and_extracts_zip(self):
        secret = "test-token-secret-value"
        with MockServer() as mock_server:
            output = self.root / "precise-output"
            code, stdout, stderr = self.run_main(
                [
                    str(self.pdf),
                    "--mode",
                    "precise",
                    "--base-url",
                    mock_server.base_url,
                    "--poll-interval",
                    "0.01",
                    "--yes",
                    "-o",
                    str(output),
                ],
                env={"MINERU_API_TOKEN": secret},
            )
            self.assertEqual(code, 0, stderr)
            self.assertNotIn(secret, stdout + stderr)
            result = json.loads(stdout)
            self.assertEqual(result["mode"], "precise")
            self.assertIn("Precise result", Path(result["markdown_path"]).read_text())
            api_requests = [r for r in mock_server.server.requests if r[1].startswith("/api/v4/")]
            self.assertTrue(api_requests)
            self.assertTrue(
                all(r[2].get("Authorization") == f"Bearer {secret}" for r in api_requests)
            )
            upload_requests = [r for r in mock_server.server.requests if r[1] == "/upload/precise"]
            self.assertEqual(upload_requests[0][2].get("Authorization"), None)
            self.assertEqual(upload_requests[0][2].get("Content-Type"), None)
            self.assertFalse((output / "mineru-result.zip").exists())

    def test_noninteractive_upload_requires_yes(self):
        output = self.root / "blocked-output"
        code, _stdout, stderr = self.run_main(
            [str(self.pdf), "--mode", "agent", "-o", str(output)]
        )
        self.assertEqual(code, 2)
        self.assertIn("--yes", stderr)
        self.assertFalse(output.exists())

    def test_token_file_may_include_a_heading(self):
        token_file = self.root / "token.md"
        token_file.write_text("MinerU API\n\nexample-token-value\n", encoding="utf-8")
        self.assertEqual(client.read_token_file(token_file), "example-token-value")

    def test_download_retries_after_transient_connection_error(self):
        destination = self.root / "download.md"
        response = io.BytesIO(b"# Retried download\n")
        with mock.patch.object(
            client.urllib.request,
            "urlopen",
            side_effect=[client.urllib.error.URLError("temporary TLS error"), response],
        ) as urlopen:
            with mock.patch.object(client.time, "sleep"):
                client.download_file("https://example.invalid/result.md", destination)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(destination.read_bytes(), b"# Retried download\n")
        self.assertFalse((self.root / "download.md.part").exists())

    def test_download_uses_curl_fallback_after_tls_failures(self):
        destination = self.root / "curl-download.md"

        def fake_curl(arguments, **_kwargs):
            if "--noproxy" not in arguments:
                return mock.Mock(returncode=35, stderr="proxy TLS failure")
            output = Path(arguments[arguments.index("--output") + 1])
            output.write_bytes(b"# curl fallback\n")
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.object(
            client.urllib.request,
            "urlopen",
            side_effect=client.urllib.error.URLError("persistent TLS error"),
        ) as urlopen:
            with mock.patch.object(client.time, "sleep"):
                with mock.patch.object(client.shutil, "which", return_value="/usr/bin/curl"):
                    with mock.patch.object(
                        client.subprocess, "run", side_effect=fake_curl
                    ) as curl_run:
                        client.download_file(
                            "https://example.invalid/result.md", destination
                        )
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(curl_run.call_count, 2)
        self.assertIn("--noproxy", curl_run.call_args_list[1].args[0])
        self.assertEqual(destination.read_bytes(), b"# curl fallback\n")

    def test_zip_path_traversal_is_rejected(self):
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../escaped.md", "bad")
        with self.assertRaises(client.MinerUError):
            client.safe_extract_zip(archive, self.root / "extract")
        self.assertFalse((self.root / "escaped.md").exists())


if __name__ == "__main__":
    unittest.main()
