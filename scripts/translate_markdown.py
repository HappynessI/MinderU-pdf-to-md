#!/usr/bin/env python3
"""Translate Markdown while preserving formulas, links, tables, and local assets."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TARGET_LANGUAGE = "zh-CN"
KEYCHAIN_SERVICE = "mineru-markdown-translate"
STATE_VERSION = 1
MODEL_ALIASES = {
    "ds-v4-flash": "deepseek-v4-flash",
    "ds-v4-pro": "deepseek-v4-pro",
}
LANGUAGE_NAMES = {
    "zh": "Simplified Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-hans": "Simplified Chinese",
    "en": "English",
    "en-us": "English",
}
LANGUAGE_SUFFIXES = {
    "zh": "CN",
    "zh-cn": "CN",
    "zh-hans": "CN",
    "en": "EN",
    "en-us": "EN",
}
REFERENCE_HEADINGS = {
    "references",
    "reference",
    "bibliography",
    "参考文献",
}


class TranslationError(RuntimeError):
    """A safe, user-facing translation error."""


@dataclass(frozen=True)
class DocumentPart:
    text: str
    translatable: bool


@dataclass(frozen=True)
class ProtectedMarkdown:
    text: str
    prefix: str
    replacements: Tuple[Tuple[str, str], ...]


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_api_key_file(path: Path) -> str:
    """Read a secret from a plain-text or small Markdown credential file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TranslationError(f"无法读取 API Key 文件：{path}: {exc}") from exc

    matches = re.findall(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9._-]{12,}", text)
    if matches:
        return matches[-1]

    assignment = re.compile(
        r"(?im)^\s*(?:export\s+)?(?:[A-Z0-9_]*(?:API_KEY|TOKEN))\s*[:=]\s*[\"']?([^\s\"']+)"
    )
    matches = assignment.findall(text)
    if matches:
        return matches[-1]

    candidates = [
        line.strip().strip("`\"'")
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "<!--"))
    ]
    for candidate in reversed(candidates):
        if len(candidate) >= 12 and not any(character.isspace() for character in candidate):
            return candidate
    raise TranslationError(f"API Key 文件中没有找到有效密钥：{path}")


def read_keychain_api_key(account: Optional[str] = None) -> Optional[str]:
    if platform.system() != "Darwin" or not shutil.which("security"):
        return None
    completed = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            account or getpass.getuser(),
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


def resolve_api_key(api_key_file: Optional[Path] = None) -> str:
    for name in ("MARKDOWN_TRANSLATION_API_KEY", "DEEPSEEK_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value

    env_file = os.environ.get("MARKDOWN_TRANSLATION_API_KEY_FILE", "").strip()
    selected_file = api_key_file or (Path(env_file).expanduser() if env_file else None)
    if selected_file:
        return read_api_key_file(selected_file.expanduser())

    keychain_value = read_keychain_api_key()
    if keychain_value:
        return keychain_value

    default_file = Path.home() / ".config" / "mineru-pdf-to-md" / "translation-token"
    if default_file.is_file():
        return read_api_key_file(default_file)

    raise TranslationError(
        "没有找到翻译 API Key；请设置 MARKDOWN_TRANSLATION_API_KEY、"
        "DEEPSEEK_API_KEY，或使用 --api-key-file"
    )


def normalize_model(model: str) -> str:
    normalized = model.strip()
    return MODEL_ALIASES.get(normalized.lower(), normalized)


def normalize_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TranslationError("翻译 API base URL 无效")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise TranslationError("远程翻译 API 必须使用 HTTPS")
    return value


def language_name(language: str) -> str:
    return LANGUAGE_NAMES.get(language.lower(), language)


def output_suffix(language: str) -> str:
    value = language.strip().lower()
    return LANGUAGE_SUFFIXES.get(value, re.sub(r"[^A-Za-z0-9]+", "-", language).strip("-") or "translated")


def load_glossary(path: Optional[Path]) -> Tuple[str, str]:
    if path is None:
        return "", ""
    try:
        text = path.expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise TranslationError(f"无法读取术语表：{path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        cleaned = text.strip()
        return cleaned, sha256_text(cleaned)
    if not isinstance(payload, dict):
        raise TranslationError("JSON 术语表必须是 source→target 对象")
    lines = [f"- {source} => {target}" for source, target in payload.items()]
    cleaned = "\n".join(lines)
    return cleaned, sha256_text(cleaned)


def _block_spans(markdown: str) -> Iterable[str]:
    """Yield blocks while retaining their exact trailing blank-line separators."""
    position = 0
    pattern = re.compile(r".*?(?:\n[ \t]*\n+|\Z)", re.DOTALL)
    for match in pattern.finditer(markdown):
        block = match.group(0)
        if not block:
            continue
        yield block
        position = match.end()
    if position < len(markdown):
        yield markdown[position:]


def _heading(block: str) -> Optional[Tuple[int, str]]:
    match = re.match(r"^[ \t]*(#{1,6})[ \t]+(.+?)[ \t]*(?:\n|$)", block)
    if not match:
        return None
    title = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2)).strip()
    return len(match.group(1)), title


def _translate_reference_heading(block: str, target_language: str) -> str:
    if target_language.lower() not in {"zh", "zh-cn", "zh-hans"}:
        return block
    return re.sub(
        r"^([ \t]*#{1,6}[ \t]+)(.+?)([ \t]*(?:\n|$))",
        lambda match: match.group(1) + "参考文献" + match.group(3),
        block,
        count=1,
    )


def document_parts(
    markdown: str,
    *,
    target_language: str,
    translate_references: bool,
) -> List[DocumentPart]:
    blocks = list(_block_spans(markdown))
    parts: List[DocumentPart] = []
    reference_level: Optional[int] = None

    for index, block in enumerate(blocks):
        heading = _heading(block)
        if heading:
            level, title = heading
            normalized_title = re.sub(r"[^\w\u4e00-\u9fff]+", " ", title.lower()).strip()
            is_reference_heading = normalized_title in REFERENCE_HEADINGS
            if is_reference_heading and not translate_references:
                reference_level = level
                parts.append(
                    DocumentPart(
                        _translate_reference_heading(block, target_language),
                        False,
                    )
                )
                continue
            if reference_level is not None and level <= reference_level:
                reference_level = None

        stripped = block.strip()
        is_frontmatter = index == 0 and stripped.startswith("---") and stripped.endswith("---")
        translatable = bool(stripped) and reference_level is None and not is_frontmatter
        parts.append(DocumentPart(block, translatable))
    return parts


def chunk_document(parts: Sequence[DocumentPart], max_chars: int) -> List[DocumentPart]:
    if max_chars < 1000:
        raise TranslationError("--max-chars 不能小于 1000")
    result: List[DocumentPart] = []
    pending: List[str] = []
    pending_chars = 0

    def flush() -> None:
        nonlocal pending_chars
        if pending:
            result.append(DocumentPart("".join(pending), True))
            pending.clear()
            pending_chars = 0

    for part in parts:
        if not part.translatable:
            flush()
            result.append(part)
            continue
        if "<table" in part.text.lower():
            # Large MinerU HTML tables contain hundreds of protected tags. Keep
            # each table isolated so the model never has to preserve several
            # unrelated tables in one response.
            flush()
            result.append(part)
            continue
        size = len(part.text)
        if pending and pending_chars + size > max_chars:
            flush()
        pending.append(part.text)
        pending_chars += size
        if pending_chars >= max_chars:
            flush()
    flush()
    return result


PROTECTED_PATTERN = re.compile(
    r"(?:```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~)"
    r"|(?:<!--.*?-->)"
    r"|(?:!\[[^\]\n]*\]\([^\n)]*\))"
    r"|(?:\$\$.*?\$\$)"
    r"|(?:\\\[.*?\\\])"
    r"|(?:\\\(.*?\\\))"
    r"|(?:(?<!\\)\$(?!\$)(?:\\.|[^$\n])+?(?<!\\)\$)"
    r"|(?:`+[^`\n]*`+)"
    r"|(?:(?<=\]\()[^)\n]+(?=\)))"
    r"|(?:</?[A-Za-z][^>]*>)"
    r"|(?:&(?:[A-Za-z][A-Za-z0-9]+|#[0-9]+|#x[0-9A-Fa-f]+);)"
    r"|(?:https?://[^\s<>()。！？；，、]+)"
    r"|(?:mailto:[^\s<>()。！？；，、]+)",
    re.DOTALL,
)


def protect_markdown(markdown: str) -> ProtectedMarkdown:
    digest = sha256_text(markdown)[:10].upper()
    prefix = f"__MDT_{digest}_"
    while prefix in markdown:
        prefix += "X"
    replacements: List[Tuple[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        placeholder = f"{prefix}{len(replacements):05d}__"
        replacements.append((placeholder, match.group(0)))
        return placeholder

    protected = PROTECTED_PATTERN.sub(replace, markdown)
    return ProtectedMarkdown(protected, prefix, tuple(replacements))


def restore_markdown(translated: str, protected: ProtectedMarkdown) -> str:
    placeholder_pattern = re.compile(re.escape(protected.prefix) + r"\d{5}__")
    expected = [placeholder for placeholder, _value in protected.replacements]
    observed = placeholder_pattern.findall(translated)
    if observed != expected:
        raise TranslationError("模型修改、遗漏或重排了 Markdown 保护占位符")
    restored = translated
    for placeholder, value in protected.replacements:
        restored = restored.replace(placeholder, value)
    return restored


def strip_accidental_fence(content: str) -> str:
    value = content.strip()
    match = re.fullmatch(
        r"```(?:markdown|md|json)?\s*\n(.*)\n```",
        value,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return content


def heading_signature(markdown: str) -> List[int]:
    return [len(match.group(1)) for match in re.finditer(r"(?m)^[ \t]*(#{1,6})[ \t]+", markdown)]


def validate_structure(source: str, translated: str) -> None:
    if heading_signature(source) != heading_signature(translated):
        raise TranslationError("翻译前后的 Markdown 标题层级不一致")
    for marker in ("<table", "</table>", "<tr", "</tr>", "<td", "</td>"):
        if source.lower().count(marker) != translated.lower().count(marker):
            raise TranslationError(f"翻译前后的 HTML 表格标记数量不一致：{marker}")


def validate_final_document(source: str, translated: str) -> None:
    """Validate invariants again after all translated and literal chunks are joined."""
    validate_structure(source, translated)
    if "__MDT_" in translated:
        raise TranslationError("最终 Markdown 中残留了保护占位符")
    source_protected = [value for _placeholder, value in protect_markdown(source).replacements]
    target_protected = [value for _placeholder, value in protect_markdown(translated).replacements]
    is_url = lambda value: value.startswith(("http://", "https://", "mailto:"))
    source_critical = [value for value in source_protected if not is_url(value)]
    target_critical = [value for value in target_protected if not is_url(value)]
    if source_critical != target_critical:
        raise TranslationError("最终 Markdown 中的公式、代码、链接、图片或 HTML 结构发生变化")
    source_urls = Counter(value for value in source_protected if is_url(value))
    target_urls = Counter(value for value in target_protected if is_url(value))
    if any(target_urls[value] < count for value, count in source_urls.items()):
        raise TranslationError("最终 Markdown 遗漏或修改了源文档中的 URL")


def preserve_outer_whitespace(source: str, translated: str) -> str:
    """Restore exact chunk-boundary whitespace that models often trim."""
    match = re.fullmatch(r"(\s*)(.*?)(\s*)", source, re.DOTALL)
    assert match is not None
    leading, _core, trailing = match.groups()
    return leading + translated.strip() + trailing


def _safe_error_detail(raw: bytes, api_key: str) -> str:
    value = raw[:600].decode("utf-8", errors="replace").replace(api_key, "<redacted>")
    return re.sub(r"\s+", " ", value).strip()


def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    markdown: str,
    timeout: float,
    retries: int,
    max_output_tokens: int,
    thinking: str,
) -> Tuple[str, Dict[str, int]]:
    url = base_url + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": markdown},
        ],
        "temperature": 0.1,
        "max_tokens": max_output_tokens,
        "stream": False,
    }
    if thinking != "auto":
        payload["thinking"] = {"type": thinking}
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Optional[BaseException] = None

    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "mineru-markdown-translate/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            decoded = json.loads(raw.decode("utf-8"))
            content = decoded["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise TranslationError("翻译 API 返回了空内容")
            usage_payload = decoded.get("usage") or {}
            usage = {
                key: int(usage_payload.get(key, 0) or 0)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            return strip_accidental_fence(content), usage
        except urllib.error.HTTPError as exc:
            detail = _safe_error_detail(exc.read(), api_key)
            last_error = TranslationError(f"翻译 API HTTP {exc.code}：{detail or 'no detail'}")
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable:
                raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = TranslationError(f"无法连接翻译 API：{exc.reason}")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            last_error = TranslationError("翻译 API 返回了无法解析的响应")

        if attempt < retries:
            wait_seconds = min(2 ** (attempt - 1), 8)
            eprint(f"翻译请求失败，{wait_seconds} 秒后重试（{attempt}/{retries}）…")
            time.sleep(wait_seconds)

    assert last_error is not None
    raise last_error


def build_system_prompt(
    *,
    source_language: str,
    target_language: str,
    glossary: str,
) -> str:
    prompt = f"""You are a professional academic translator.
Translate the supplied Markdown fragment from {language_name(source_language)} to {language_name(target_language)}.

Mandatory rules:
1. Return only the translated Markdown fragment. Do not add code fences, explanations, summaries, or commentary.
2. Preserve every placeholder beginning with __MDT_ exactly once and in the original order.
3. Preserve Markdown structure, heading levels, list numbering, paragraph order, HTML table structure, and line breaks where practical.
4. Translate all natural-language prose faithfully without omission, condensation, expansion, or factual correction.
5. Keep author names, model names, dataset names, citations, variable names, abbreviations, URLs, and numeric values unchanged unless a conventional translated name is unambiguous.
6. Use concise, publication-quality academic language and consistent terminology.
"""
    if glossary:
        prompt += "\nRequired terminology:\n" + glossary + "\n"
    return prompt


def is_html_table_fragment(markdown: str) -> bool:
    return bool(re.fullmatch(r"\s*<table\b.*</table>\s*", markdown, re.DOTALL | re.IGNORECASE))


def translate_html_table(
    source: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    source_language: str,
    target_language: str,
    glossary: str,
    timeout: float,
    retries: int,
    max_output_tokens: int,
    thinking: str,
) -> Tuple[str, Dict[str, int], int]:
    """Translate visible table text as an ID-addressed JSON batch."""
    pieces = re.split(r"(<[^>]+>)", source)
    nodes: List[Tuple[int, str, str, ProtectedMarkdown]] = []
    for index, piece in enumerate(pieces):
        if not piece or piece.startswith("<"):
            continue
        match = re.fullmatch(r"(\s*)(.*?)(\s*)", piece, re.DOTALL)
        assert match is not None
        leading, content, trailing = match.groups()
        if not content or not re.search(r"[A-Za-z\u4e00-\u9fff]", content):
            continue
        stripped = content.strip()
        if re.fullmatch(r"[A-Z][A-Z0-9._/+\-]{1,15}", stripped):
            continue
        if "." in stripped and re.fullmatch(
            r"[A-Za-z]{1,8}\.(?:\s+[A-Za-z]{1,8}\.)*", stripped
        ):
            continue
        nodes.append((index, leading, trailing, protect_markdown(content)))

    if not nodes:
        return source, {}, 0

    request_payload = {
        "cells": [
            {"id": cell_id, "text": protected.text}
            for cell_id, (_index, _leading, _trailing, protected) in enumerate(nodes)
        ]
    }
    table_prompt = f"""You are a professional academic table translator.
Translate every `text` value in the supplied JSON from {language_name(source_language)} to {language_name(target_language)}.
Return only strict JSON in this exact shape: {{"translations":[{{"id":0,"text":"..."}}]}}.
Keep exactly the same integer IDs in the same order. Do not omit, merge, or add entries.
Preserve every __MDT_ placeholder exactly once and in its original position within that entry.
Keep model names, dataset names, abbreviations, citations, variables, and numeric values unchanged.
Use neighboring cells for context and concise publication-quality terminology.
"""
    if glossary:
        table_prompt += "\nRequired terminology:\n" + glossary + "\n"

    total_usage: Dict[str, int] = {}
    api_calls = 0
    last_error: Optional[TranslationError] = None
    for structure_attempt in range(1, 3):
        raw, usage = chat_completion(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt=table_prompt,
            markdown=json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")),
            timeout=timeout,
            retries=retries,
            max_output_tokens=max_output_tokens,
            thinking=thinking,
        )
        api_calls += 1
        add_usage(total_usage, usage)
        try:
            decoded = json.loads(raw)
            translations = decoded["translations"]
            if not isinstance(translations, list):
                raise TranslationError("表格翻译响应的 translations 不是数组")
            observed_ids = [item.get("id") for item in translations if isinstance(item, dict)]
            expected_ids = list(range(len(nodes)))
            if observed_ids != expected_ids or len(translations) != len(nodes):
                raise TranslationError("表格翻译响应遗漏、增加或重排了单元格")

            translated_pieces = list(pieces)
            for item, (piece_index, leading, trailing, protected) in zip(translations, nodes):
                value = item.get("text")
                if not isinstance(value, str):
                    raise TranslationError("表格翻译响应包含非文本单元格")
                translated_pieces[piece_index] = leading + restore_markdown(value, protected) + trailing
            translated = "".join(translated_pieces)
            validate_structure(source, translated)
            return translated, total_usage, api_calls
        except (json.JSONDecodeError, KeyError, TypeError, TranslationError) as exc:
            last_error = exc if isinstance(exc, TranslationError) else TranslationError("表格翻译 API 返回了无效 JSON")
            if structure_attempt < 2:
                eprint("模型返回的表格单元格结构无效，正在重试该表格…")

    assert last_error is not None
    raise last_error


def load_state(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"version": STATE_VERSION, "chunks": {}, "usage": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "chunks": {}, "usage": {}}
    if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "chunks": {}, "usage": {}}
    if not isinstance(payload.get("chunks"), dict):
        payload["chunks"] = {}
    if not isinstance(payload.get("usage"), dict):
        payload["usage"] = {}
    return payload


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(value, encoding="utf-8")
    os.replace(str(temporary), str(path))


def save_state(path: Path, state: Dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def cache_key(
    source: str,
    *,
    base_url: str,
    model: str,
    source_language: str,
    target_language: str,
    glossary_hash: str,
    thinking: str,
    processor_version: str,
) -> str:
    payload = {
        "source": source,
        "base_url": base_url,
        "model": model,
        "source_language": source_language,
        "target_language": target_language,
        "glossary_hash": glossary_hash,
        "thinking": thinking,
    }
    if processor_version:
        payload["processor_version"] = processor_version
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def add_usage(total: Dict[str, int], current: Dict[str, int]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = int(total.get(key, 0) or 0) + int(current.get(key, 0) or 0)


def local_asset_paths(markdown: str, source_dir: Path) -> List[Tuple[Path, Path]]:
    raw_paths: List[str] = []
    raw_paths.extend(
        match.group(1).strip("<>")
        for match in re.finditer(r"!\[[^\]\n]*\]\(([^\s)]+)", markdown)
    )
    raw_paths.extend(
        match.group(2)
        for match in re.finditer(
            r"<img\b[^>]*\bsrc\s*=\s*([\"'])(.*?)\1",
            markdown,
            re.IGNORECASE,
        )
    )

    source_root = source_dir.resolve()
    result: List[Tuple[Path, Path]] = []
    seen: set[str] = set()
    for raw in raw_paths:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme or parsed.netloc or raw.startswith(("#", "/")):
            continue
        relative_text = urllib.parse.unquote(parsed.path)
        if not relative_text or relative_text in seen:
            continue
        candidate = (source_dir / relative_text).resolve()
        try:
            relative = candidate.relative_to(source_root)
        except ValueError:
            eprint(f"警告：跳过源目录之外的资源：{raw}")
            continue
        if candidate.is_file():
            seen.add(relative_text)
            result.append((candidate, relative))
        else:
            eprint(f"警告：Markdown 引用的本地资源不存在：{raw}")
    return result


def copy_assets(markdown: str, source: Path, output_dir: Path) -> int:
    count = 0
    for asset, relative in local_asset_paths(markdown, source.parent):
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or asset.read_bytes() != destination.read_bytes():
            shutil.copy2(asset, destination)
        count += 1
    return count


def confirm_cloud_translation(args: argparse.Namespace, source: Path) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise TranslationError("非交互调用必须传入 --yes 才能把 Markdown 发送到翻译 API")
    answer = input(
        f"将把 {source} 的文本内容发送到 {args.base_url}。继续吗？[y/N] "
    ).strip().lower()
    if answer not in {"y", "yes"}:
        raise TranslationError("已取消翻译")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用 OpenAI-compatible API 翻译 Markdown，并保护公式、链接、表格和图片"
    )
    parser.add_argument("input", type=Path, help="待翻译的 Markdown 文件")
    parser.add_argument("-o", "--output-dir", type=Path, help="独立翻译输出目录")
    parser.add_argument("--output-name", help="输出 Markdown 文件名")
    parser.add_argument("--source-language", default="en", help="源语言，默认 en")
    parser.add_argument(
        "--target-language", default=DEFAULT_TARGET_LANGUAGE, help="目标语言，默认 zh-CN"
    )
    parser.add_argument("--base-url", default=os.environ.get("MARKDOWN_TRANSLATION_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--model",
        default=os.environ.get("MARKDOWN_TRANSLATION_MODEL", DEFAULT_MODEL),
        help=f"模型名，默认 {DEFAULT_MODEL}",
    )
    parser.add_argument("--api-key-file", type=Path, help="API Key 文件；不会写入输出")
    parser.add_argument("--glossary-file", type=Path, help="可选 JSON 或文本术语表")
    parser.add_argument("--max-chars", type=int, default=12000, help="每个翻译块的近似字符上限")
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument(
        "--thinking",
        choices=["disabled", "enabled", "auto"],
        default="disabled",
        help="默认关闭 DeepSeek thinking 以降低翻译成本；其他服务可使用 auto",
    )
    parser.add_argument("--request-timeout", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--translate-references", action="store_true", help="同时翻译参考文献条目")
    parser.add_argument("--no-copy-assets", action="store_true", help="不复制 Markdown 引用的本地资源")
    parser.add_argument("--force", action="store_true", help="忽略已有翻译缓存并重新调用 API")
    parser.add_argument("--yes", action="store_true", help="确认把 Markdown 文本发送到云端翻译 API")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = args.input.expanduser().resolve()
        if not source.is_file():
            raise TranslationError(f"Markdown 文件不存在：{source}")
        if source.suffix.lower() not in {".md", ".markdown"}:
            raise TranslationError("输入文件必须是 .md 或 .markdown")
        markdown = source.read_text(encoding="utf-8")
        if not markdown.strip():
            raise TranslationError("输入 Markdown 为空")

        args.base_url = normalize_base_url(args.base_url)
        model = normalize_model(args.model)
        suffix = output_suffix(args.target_language)
        output_dir = (
            args.output_dir.expanduser().resolve()
            if args.output_dir
            else source.parent / f"translation-{args.target_language}"
        )
        output_name = args.output_name or f"{source.stem}-{suffix}{source.suffix}"
        output_path = output_dir / output_name
        if output_path.resolve() == source:
            raise TranslationError("输出文件不能覆盖输入 Markdown")
        output_dir.mkdir(parents=True, exist_ok=True)
        state_path = output_dir / ".translation-state.json"

        glossary, glossary_hash = load_glossary(args.glossary_file)
        parts = document_parts(
            markdown,
            target_language=args.target_language,
            translate_references=args.translate_references,
        )
        chunks = chunk_document(parts, args.max_chars)
        translatable_count = sum(part.translatable for part in chunks)
        if translatable_count == 0:
            raise TranslationError("没有找到可翻译的 Markdown 内容")

        confirm_cloud_translation(args, source)
        api_key = resolve_api_key(args.api_key_file)
        state = (
            {"version": STATE_VERSION, "chunks": {}, "usage": {}}
            if args.force
            else load_state(state_path)
        )
        state.update(
            {
                "version": STATE_VERSION,
                "source_path": str(source),
                "source_sha256": sha256_text(markdown),
                "target_language": args.target_language,
                "model": model,
                "base_url": args.base_url,
            }
        )
        state.setdefault("chunks", {})
        state.setdefault("usage", {})
        prompt = build_system_prompt(
            source_language=args.source_language,
            target_language=args.target_language,
            glossary=glossary,
        )

        output_parts: List[str] = []
        translated_index = 0
        api_calls = 0
        cache_hits = 0
        for part in chunks:
            if not part.translatable:
                output_parts.append(part.text)
                continue
            translated_index += 1
            key = cache_key(
                part.text,
                base_url=args.base_url,
                model=model,
                source_language=args.source_language,
                target_language=args.target_language,
                glossary_hash=glossary_hash,
                thinking=args.thinking,
                processor_version=(
                    "html-table-v2" if is_html_table_fragment(part.text) else ""
                ),
            )
            cached = state["chunks"].get(key)
            if isinstance(cached, dict) and isinstance(cached.get("translation"), str):
                translated = preserve_outer_whitespace(part.text, cached["translation"])
                cache_hits += 1
                eprint(f"复用翻译块 {translated_index}/{translatable_count}")
            else:
                eprint(f"正在翻译块 {translated_index}/{translatable_count}…")
                if is_html_table_fragment(part.text):
                    translated, usage, table_api_calls = translate_html_table(
                        part.text,
                        base_url=args.base_url,
                        api_key=api_key,
                        model=model,
                        source_language=args.source_language,
                        target_language=args.target_language,
                        glossary=glossary,
                        timeout=args.request_timeout,
                        retries=max(1, args.retries),
                        max_output_tokens=args.max_output_tokens,
                        thinking=args.thinking,
                    )
                    api_calls += table_api_calls
                    add_usage(state["usage"], usage)
                else:
                    protected = protect_markdown(part.text)
                    last_structure_error: Optional[TranslationError] = None
                    for structure_attempt in range(1, 3):
                        raw_translation, usage = chat_completion(
                            base_url=args.base_url,
                            api_key=api_key,
                            model=model,
                            system_prompt=prompt,
                            markdown=protected.text,
                            timeout=args.request_timeout,
                            retries=max(1, args.retries),
                            max_output_tokens=args.max_output_tokens,
                            thinking=args.thinking,
                        )
                        api_calls += 1
                        add_usage(state["usage"], usage)
                        try:
                            translated = restore_markdown(raw_translation, protected)
                            validate_structure(part.text, translated)
                            break
                        except TranslationError as exc:
                            last_structure_error = exc
                            if structure_attempt < 2:
                                eprint("模型破坏了 Markdown 结构，正在重试该块…")
                    else:
                        assert last_structure_error is not None
                        raise last_structure_error

                translated = preserve_outer_whitespace(part.text, translated)
                validate_structure(part.text, translated)

                state["chunks"][key] = {
                    "source_sha256": sha256_text(part.text),
                    "translation": translated,
                }
                save_state(state_path, state)
            output_parts.append(translated)

        translated_markdown = "".join(output_parts)
        if not translated_markdown.strip():
            raise TranslationError("翻译结果为空")
        validate_final_document(markdown, translated_markdown)
        atomic_write_text(output_path, translated_markdown)
        copied_assets = 0 if args.no_copy_assets else copy_assets(markdown, source, output_dir)
        state["completed"] = True
        state["output_path"] = str(output_path.resolve())
        save_state(state_path, state)

        result = {
            "source_path": str(source),
            "output_dir": str(output_dir.resolve()),
            "markdown_path": str(output_path.resolve()),
            "state_path": str(state_path.resolve()),
            "model": model,
            "target_language": args.target_language,
            "thinking": args.thinking,
            "translation_chunks": translatable_count,
            "api_calls": api_calls,
            "cache_hits": cache_hits,
            "copied_assets": copied_assets,
            "usage": state.get("usage", {}),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (TranslationError, OSError, UnicodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
