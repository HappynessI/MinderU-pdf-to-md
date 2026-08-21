#!/usr/bin/env python3
"""Convert a local PDF to Markdown with MinerU's official cloud APIs."""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_BASE_URL = "https://mineru.net"
KEYCHAIN_SERVICE = "mineru-pdf-to-md"
AGENT_MAX_BYTES = 10 * 1024 * 1024
PRECISE_MAX_BYTES = 200 * 1024 * 1024
TERMINAL_STATES = {"done", "failed"}


class MinerUError(RuntimeError):
    """A safe, user-facing MinerU client error."""


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def read_token_file(path: Path) -> str:
    """Read a token without assuming the file contains only the token."""
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError as exc:
        raise MinerUError(f"无法读取 Token 文件：{path}: {exc}") from exc
    values = [line for line in lines if line]
    if not values:
        raise MinerUError(f"Token 文件为空：{path}")
    for value in reversed(values):
        if value.startswith("sk-"):
            return value
    return values[-1]


def read_keychain_token(account: Optional[str] = None) -> Optional[str]:
    if platform.system() != "Darwin" or not shutil.which("security"):
        return None
    resolved_account = account or getpass.getuser()
    completed = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            resolved_account,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def resolve_token(token_file: Optional[Path] = None) -> Optional[str]:
    env_token = os.environ.get("MINERU_API_TOKEN", "").strip()
    if env_token:
        return env_token

    env_file = os.environ.get("MINERU_API_TOKEN_FILE", "").strip()
    selected_file = token_file or (Path(env_file).expanduser() if env_file else None)
    if selected_file:
        return read_token_file(selected_file.expanduser())

    keychain_token = read_keychain_token()
    if keychain_token:
        return keychain_token

    default_file = Path.home() / ".config" / "mineru-pdf-to-md" / "token"
    if default_file.is_file():
        return read_token_file(default_file)
    return None


def _decode_json_response(raw: bytes, source: str) -> Dict[str, Any]:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        preview = raw[:300].decode("utf-8", errors="replace")
        raise MinerUError(f"{source} 返回了无效 JSON：{preview}") from exc
    if not isinstance(decoded, dict):
        raise MinerUError(f"{source} 返回的 JSON 不是对象")
    if decoded.get("code") not in (None, 0):
        raise MinerUError(f"MinerU API 错误：{decoded.get('msg', 'unknown error')}")
    return decoded


def request_json(
    method: str,
    url: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "mineru-pdf-to-md/1.0",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        preview = raw[:500].decode("utf-8", errors="replace")
        raise MinerUError(f"HTTP {exc.code}：{preview}") from exc
    except urllib.error.URLError as exc:
        raise MinerUError(f"无法连接 MinerU：{exc.reason}") from exc
    return _decode_json_response(raw, url)


def stream_put(url: str, source: Path, timeout: float = 120.0) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MinerUError("MinerU 返回了无效的上传 URL")
    connection_cls = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_cls(parsed.hostname, parsed.port, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    size = source.stat().st_size
    try:
        connection.putrequest("PUT", target, skip_accept_encoding=True)
        connection.putheader("Content-Length", str(size))
        connection.putheader("User-Agent", "mineru-pdf-to-md/1.0")
        connection.endheaders()
        with source.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                connection.send(chunk)
        response = connection.getresponse()
        body = response.read(500)
        if response.status not in {200, 201, 204}:
            preview = body.decode("utf-8", errors="replace")
            raise MinerUError(f"上传失败，HTTP {response.status}：{preview}")
    except OSError as exc:
        raise MinerUError(f"上传 PDF 失败：{exc}") from exc
    finally:
        connection.close()


def download_file(
    url: str,
    destination: Path,
    timeout: float = 120.0,
    retries: int = 3,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    last_error: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": "mineru-pdf-to-md/1.0"}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            os.replace(str(temporary), str(destination))
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < retries:
                eprint(f"下载连接中断，正在重试（{attempt}/{retries}）…")
                time.sleep(min(2 ** (attempt - 1), 4))
    curl = shutil.which("curl")
    if curl:
        eprint("Python TLS 下载持续失败，正在使用系统 curl 重试…")
        base_command = [
            curl,
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "2",
            "--connect-timeout",
            str(max(1, int(timeout))),
            "--max-time",
            str(max(1, int(timeout))),
            "--output",
            str(temporary),
        ]
        completed = None
        for proxy_options in ([], ["--noproxy", "*"]):
            completed = subprocess.run(
                base_command + proxy_options + [url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if completed.returncode == 0 and temporary.is_file():
                os.replace(str(temporary), str(destination))
                return
            temporary.unlink(missing_ok=True)
            if not proxy_options:
                eprint("curl 经代理下载失败，正在尝试直连结果存储…")
        assert completed is not None
        detail = (completed.stderr or "").replace(url, "<signed-url>").strip()
        if len(detail) > 300:
            detail = detail[:300] + "…"
        if detail:
            raise MinerUError(
                f"下载 MinerU 结果失败：curl {completed.returncode}: {detail}"
            ) from last_error
    raise MinerUError(f"下载 MinerU 结果失败：{last_error}") from last_error


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination_root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise MinerUError(f"结果 ZIP 包含符号链接：{member.filename}")
            candidate = (destination / member.filename).resolve()
            try:
                common = os.path.commonpath([str(destination_root), str(candidate)])
            except ValueError as exc:
                raise MinerUError("结果 ZIP 包含非法路径") from exc
            if common != str(destination_root):
                raise MinerUError(f"结果 ZIP 包含越界路径：{member.filename}")
        bundle.extractall(destination)


def find_markdown(destination: Path) -> Path:
    exact = sorted(destination.rglob("full.md"), key=lambda item: (len(item.parts), str(item)))
    if exact:
        return exact[0]
    candidates = sorted(destination.rglob("*.md"), key=lambda item: (len(item.parts), str(item)))
    if not candidates:
        raise MinerUError("MinerU 结果中没有找到 Markdown 文件")
    return candidates[0]


def _wait(deadline: float, interval: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MinerUError("等待 MinerU 任务超时")
    time.sleep(min(interval, remaining))


def poll_agent(
    base_url: str,
    task_id: str,
    *,
    deadline: float,
    interval: float,
    request_timeout: float,
) -> str:
    last_state = None
    endpoint = f"{base_url}/api/v1/agent/parse/{urllib.parse.quote(task_id)}"
    while True:
        response = request_json("GET", endpoint, timeout=request_timeout)
        data = response.get("data") or {}
        state = data.get("state")
        if state != last_state:
            eprint(f"MinerU 轻量任务状态：{state or 'unknown'}")
            last_state = state
        if state == "done":
            markdown_url = data.get("markdown_url")
            if not markdown_url:
                raise MinerUError("任务完成但未返回 markdown_url")
            return str(markdown_url)
        if state == "failed":
            raise MinerUError(f"MinerU 解析失败：{data.get('err_msg', 'unknown error')}")
        _wait(deadline, interval)


def _select_batch_result(data: Dict[str, Any], file_name: str) -> Dict[str, Any]:
    results = data.get("extract_result") or []
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list) or not results:
        return {}
    for result in results:
        if isinstance(result, dict) and result.get("file_name") == file_name:
            return result
    first = results[0]
    return first if isinstance(first, dict) else {}


def poll_precise(
    base_url: str,
    batch_id: str,
    file_name: str,
    token: str,
    *,
    deadline: float,
    interval: float,
    request_timeout: float,
) -> str:
    endpoint = (
        f"{base_url}/api/v4/extract-results/batch/{urllib.parse.quote(batch_id)}"
    )
    last_state = None
    while True:
        response = request_json("GET", endpoint, token=token, timeout=request_timeout)
        data = response.get("data") or {}
        result = _select_batch_result(data, file_name)
        state = result.get("state")
        if state != last_state:
            eprint(f"MinerU 精准任务状态：{state or 'waiting-file'}")
            last_state = state
        if state == "done":
            zip_url = result.get("full_zip_url")
            if not zip_url:
                raise MinerUError("任务完成但未返回 full_zip_url")
            return str(zip_url)
        if state == "failed":
            raise MinerUError(f"MinerU 解析失败：{result.get('err_msg', 'unknown error')}")
        _wait(deadline, interval)


def convert_agent(args: argparse.Namespace, source: Path, destination: Path) -> Dict[str, Any]:
    if source.stat().st_size > AGENT_MAX_BYTES:
        raise MinerUError("Agent 轻量 API 仅支持不超过 10MB 的文件，请使用精准模式")
    file_spec: Dict[str, Any] = {
        "file_name": source.name,
        "language": args.language,
        "enable_table": not args.no_table,
        "is_ocr": args.ocr,
        "enable_formula": not args.no_formula,
    }
    if args.page_range:
        file_spec["page_range"] = args.page_range
    response = request_json(
        "POST",
        f"{args.base_url}/api/v1/agent/parse/file",
        payload=file_spec,
        timeout=args.request_timeout,
    )
    data = response.get("data") or {}
    task_id = str(data.get("task_id") or "")
    upload_url = str(data.get("file_url") or "")
    if not task_id or not upload_url:
        raise MinerUError("轻量 API 未返回 task_id 或 file_url")
    eprint("正在上传 PDF 到 MinerU Agent 轻量 API…")
    stream_put(upload_url, source, timeout=args.request_timeout)
    markdown_url = poll_agent(
        args.base_url,
        task_id,
        deadline=time.monotonic() + args.timeout,
        interval=args.poll_interval,
        request_timeout=args.request_timeout,
    )
    markdown_path = destination / "full.md"
    download_file(markdown_url, markdown_path, timeout=args.request_timeout)
    return {
        "mode": "agent",
        "task_id": task_id,
        "output_dir": str(destination.resolve()),
        "markdown_path": str(markdown_path.resolve()),
    }


def convert_precise(
    args: argparse.Namespace,
    source: Path,
    destination: Path,
    token: str,
) -> Dict[str, Any]:
    if source.stat().st_size > PRECISE_MAX_BYTES:
        raise MinerUError("精准 API 仅支持不超过 200MB 的文件")
    file_spec: Dict[str, Any] = {
        "name": source.name,
        "data_id": args.data_id or source.stem[:120],
        "is_ocr": args.ocr,
    }
    if args.page_range:
        file_spec["page_ranges"] = args.page_range
    payload: Dict[str, Any] = {
        "files": [file_spec],
        "model_version": args.model,
        "language": args.language,
        "enable_table": not args.no_table,
        "enable_formula": not args.no_formula,
    }
    response = request_json(
        "POST",
        f"{args.base_url}/api/v4/file-urls/batch",
        payload=payload,
        token=token,
        timeout=args.request_timeout,
    )
    data = response.get("data") or {}
    batch_id = str(data.get("batch_id") or "")
    upload_urls = data.get("file_urls") or []
    if not batch_id or not isinstance(upload_urls, list) or not upload_urls:
        raise MinerUError("精准 API 未返回 batch_id 或 file_urls")
    eprint("正在上传 PDF 到 MinerU 精准 API…")
    stream_put(str(upload_urls[0]), source, timeout=args.request_timeout)
    zip_url = poll_precise(
        args.base_url,
        batch_id,
        source.name,
        token,
        deadline=time.monotonic() + args.timeout,
        interval=args.poll_interval,
        request_timeout=args.request_timeout,
    )
    archive = destination / "mineru-result.zip"
    download_file(zip_url, archive, timeout=args.request_timeout)
    safe_extract_zip(archive, destination)
    markdown_path = find_markdown(destination)
    if not args.keep_zip:
        archive.unlink(missing_ok=True)
    return {
        "mode": "precise",
        "batch_id": batch_id,
        "model": args.model,
        "output_dir": str(destination.resolve()),
        "markdown_path": str(markdown_path.resolve()),
    }


def confirm_upload(source: Path, confirmed: bool) -> None:
    if confirmed:
        return
    message = f"将把 {source} 上传到 MinerU 官方云服务 mineru.net。"
    if not sys.stdin.isatty():
        raise MinerUError(message + " 非交互调用必须显式添加 --yes。")
    answer = input(message + " 是否继续？[y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        raise MinerUError("用户取消了上传")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过 MinerU 官方云 API 将本地 PDF 转换为 Markdown"
    )
    parser.add_argument("input", type=Path, help="本地 PDF 路径")
    parser.add_argument("-o", "--output", type=Path, help="输出目录")
    parser.add_argument(
        "--mode", choices=["auto", "agent", "precise"], default="auto",
        help="auto 有 Token 时用精准 API，否则用免 Token 轻量 API",
    )
    parser.add_argument("--model", choices=["pipeline", "vlm"], default="vlm")
    parser.add_argument("--language", default="ch")
    parser.add_argument("--ocr", action="store_true", help="强制启用 OCR")
    parser.add_argument("--page-range", help="页码范围，例如 1-10")
    parser.add_argument("--no-table", action="store_true", help="关闭表格识别")
    parser.add_argument("--no-formula", action="store_true", help="关闭公式识别")
    parser.add_argument("--data-id", help="精准 API 的业务数据标识")
    parser.add_argument("--token-file", type=Path, help="精准 API Token 文件")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=900.0, help="任务总等待秒数")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="轮询间隔秒数")
    parser.add_argument("--request-timeout", type=float, default=120.0, help="单次请求超时秒数")
    parser.add_argument("--force", action="store_true", help="允许写入非空输出目录")
    parser.add_argument("--keep-zip", action="store_true", help="保留精准 API 的结果 ZIP")
    parser.add_argument("--yes", action="store_true", help="确认上传文件到 MinerU")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    args.base_url = args.base_url.rstrip("/")
    source = args.input.expanduser().resolve()
    try:
        if not source.is_file():
            raise MinerUError(f"输入文件不存在：{source}")
        if source.suffix.lower() != ".pdf":
            raise MinerUError("当前 skill 仅接受 PDF 文件")
        if args.timeout <= 0 or args.poll_interval <= 0 or args.request_timeout <= 0:
            raise MinerUError("超时和轮询参数必须大于 0")

        token = resolve_token(args.token_file)
        mode = args.mode
        if mode == "auto":
            mode = "precise" if token else "agent"
        if mode == "precise" and not token:
            raise MinerUError(
                "精准模式需要 Token。请运行 scripts/configure_token.py，"
                "或设置 MINERU_API_TOKEN。"
            )

        confirm_upload(source, args.yes)
        destination = (
            args.output.expanduser().resolve()
            if args.output
            else Path.cwd() / f"{source.stem}-mineru"
        )
        destination.mkdir(parents=True, exist_ok=True)
        if any(destination.iterdir()) and not args.force:
            raise MinerUError(f"输出目录非空：{destination}；如需写入请添加 --force")

        if mode == "precise":
            result = convert_precise(args, source, destination, str(token))
        else:
            result = convert_agent(args, source, destination)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except MinerUError as exc:
        eprint(f"错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
