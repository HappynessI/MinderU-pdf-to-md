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
DEFAULT_DO_NOT_TRANSLATE_FILE = Path(__file__).resolve().parents[1] / "do-not-translate.md"
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
MARKDOWN_IMAGE_RE = re.compile(
    r"(!\[[^\]\n]*\]\()(?P<destination><[^>\n]+>|[^\s)]+)(?P<tail>[^)\n]*\))"
)
HTML_IMAGE_RE = re.compile(
    r"(?P<prefix><img\b[^>]*\bsrc\s*=\s*)(?P<quote>[\"'])(?P<destination>.*?)(?P=quote)",
    re.IGNORECASE,
)


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
    ordered_placeholders: Tuple[str, ...]


@dataclass(frozen=True)
class TableImageConversion:
    markdown: str
    mode: str
    image_count: int
    content_list_path: Optional[Path]


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


def load_do_not_translate(path: Optional[Path]) -> Tuple[str, Tuple[str, ...], str]:
    if path is None:
        return "", (), ""
    try:
        text = path.expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise TranslationError(f"无法读取不翻译词汇表：{path}: {exc}") from exc

    terms: List[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^\s*[-*+]\s+(.+?)\s*$", line)
        if not match:
            continue
        term = match.group(1).strip()
        if len(term) >= 2 and term.startswith("`") and term.endswith("`"):
            term = term[1:-1].strip()
        if term and term not in seen:
            seen.add(term)
            terms.append(term)

    normalized = "\n".join(f"- `{term}`" for term in terms)
    return normalized, tuple(terms), sha256_text(normalized)


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


HTML_TABLE_PATTERN = re.compile(r"<table\b[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
HTML_CAPTION_PATTERN = re.compile(
    r"(<caption\b[^>]*>)(.*?)(</caption\s*>)",
    re.DOTALL | re.IGNORECASE,
)


def _content_list_candidates(source: Path, selected: Optional[Path]) -> List[Path]:
    if selected is not None:
        return [selected.expanduser().resolve()]
    return sorted(source.parent.glob("*_content_list.json"))


def _table_image_map(path: Path, source_dir: Path) -> Dict[str, List[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslationError(f"无法读取 MinerU content list：{path}: {exc}") from exc
    if not isinstance(payload, list):
        raise TranslationError(f"MinerU content list 顶层必须是数组：{path}")

    source_root = source_dir.resolve()
    result: Dict[str, List[str]] = {}
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "table":
            continue
        table_body = item.get("table_body")
        image_path = item.get("img_path")
        if not isinstance(table_body, str) or not isinstance(image_path, str):
            continue
        parsed = urllib.parse.urlsplit(image_path)
        if parsed.scheme or parsed.netloc or image_path.startswith(("#", "/")):
            continue
        relative_text = urllib.parse.unquote(parsed.path)
        candidate = (source_dir / relative_text).resolve()
        try:
            candidate.relative_to(source_root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        encoded_path = urllib.parse.quote(relative_text, safe="/:@-._~")
        result.setdefault(table_body, []).append(encoded_path)
    return result


def replace_html_tables_with_images(
    markdown: str,
    source: Path,
    *,
    table_mode: str,
    content_list_path: Optional[Path],
) -> TableImageConversion:
    if table_mode == "html":
        return TableImageConversion(markdown, "html", 0, None)

    tables = list(HTML_TABLE_PATTERN.finditer(markdown))
    if not tables:
        return TableImageConversion(markdown, "html", 0, None)

    errors: List[str] = []
    for candidate_path in _content_list_candidates(source, content_list_path):
        if not candidate_path.is_file():
            errors.append(f"文件不存在：{candidate_path}")
            continue
        try:
            available = _table_image_map(candidate_path, source.parent)
        except TranslationError as exc:
            errors.append(str(exc))
            continue

        replacements: List[str] = []
        matched = True
        for table in tables:
            images = available.get(table.group(0))
            if not images:
                matched = False
                break
            replacements.append(images.pop(0))
        if not matched:
            errors.append(f"表格正文或截图不完整：{candidate_path}")
            continue

        replacement_iter = iter(replacements)
        converted = HTML_TABLE_PATTERN.sub(
            lambda _match: f"![]({next(replacement_iter)})", markdown
        )
        return TableImageConversion(
            converted,
            "image",
            len(replacements),
            candidate_path.resolve(),
        )

    detail = "；".join(errors) if errors else "未找到 *_content_list.json"
    if table_mode == "image":
        raise TranslationError(f"无法把 HTML 表格替换为 MinerU 截图：{detail}")
    eprint(f"警告：无法使用 MinerU 表格截图，回退为原始 HTML：{detail}")
    return TableImageConversion(markdown, "html", 0, None)


def _is_markdown_table_row(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and "|" in stripped


def _is_markdown_table_separator(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells = [cell.strip() for cell in stripped.split("|")]
    return len(cells) >= 2 and all(
        re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells
    )


def _markdown_table_spans(markdown: str) -> List[Tuple[int, int]]:
    lines = markdown.splitlines(keepends=True)
    offsets: List[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)

    spans: List[Tuple[int, int]] = []
    index = 0
    while index + 1 < len(lines):
        if not (
            _is_markdown_table_row(lines[index])
            and _is_markdown_table_separator(lines[index + 1])
        ):
            index += 1
            continue

        end_index = index + 2
        while end_index < len(lines) and _is_markdown_table_row(lines[end_index]):
            end_index += 1
        start = offsets[index]
        end = offsets[end_index] if end_index < len(lines) else len(markdown)
        spans.append((start, end))
        index = end_index
    return spans


def _table_spans(markdown: str) -> List[Tuple[int, int]]:
    spans = [(match.start(), match.end()) for match in HTML_TABLE_PATTERN.finditer(markdown)]
    spans.extend(_markdown_table_spans(markdown))
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _split_table_segments(markdown: str) -> Iterable[Tuple[str, bool]]:
    """Yield exact text segments with table bodies marked non-translatable."""
    position = 0
    for start, end in _table_spans(markdown):
        if start > position:
            yield markdown[position:start], True
        table = markdown[start:end]
        if table.lstrip().lower().startswith("<table"):
            table_position = 0
            for caption in HTML_CAPTION_PATTERN.finditer(table):
                if caption.start() > table_position:
                    yield table[table_position:caption.start()], False
                yield caption.group(1), False
                if caption.group(2):
                    yield caption.group(2), True
                yield caption.group(3), False
                table_position = caption.end()
            if table_position < len(table):
                yield table[table_position:], False
        else:
            yield table, False
        position = end
    if position < len(markdown):
        yield markdown[position:], True


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
        if not translatable:
            parts.append(DocumentPart(block, False))
            continue
        for segment, segment_translatable in _split_table_segments(block):
            parts.append(
                DocumentPart(
                    segment,
                    segment_translatable and bool(segment.strip()),
                )
            )
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


def _protected_value_may_reorder(value: str, terms: Sequence[str] = ()) -> bool:
    if value in terms:
        return True
    if value.startswith(("$$", r"\[", r"\(", "$")):
        return True
    return value.startswith("`") and not value.startswith("```")


def protect_markdown(
    markdown: str, do_not_translate_terms: Sequence[str] = ()
) -> ProtectedMarkdown:
    digest = sha256_text(markdown)[:10].upper()
    prefix = f"__MDT_{digest}_"
    while prefix in markdown:
        prefix += "X"
    replacements: List[Tuple[str, str]] = []
    ordered_placeholders: List[str] = []
    terms = tuple(do_not_translate_terms)

    def replace(match: re.Match[str]) -> str:
        placeholder = f"{prefix}{len(replacements):05d}__"
        value = match.group(0)
        replacements.append((placeholder, value))
        if not _protected_value_may_reorder(value, terms):
            ordered_placeholders.append(placeholder)
        return placeholder

    term_patterns: List[str] = []
    for term in sorted(do_not_translate_terms, key=len, reverse=True):
        escaped = re.escape(term)
        if term[0].isascii() and (term[0].isalnum() or term[0] == "_"):
            escaped = r"(?<![A-Za-z0-9_])" + escaped
        if term[-1].isascii() and (term[-1].isalnum() or term[-1] == "_"):
            escaped += r"(?![A-Za-z0-9_])"
        term_patterns.append(escaped)

    pattern = PROTECTED_PATTERN
    if term_patterns:
        pattern = re.compile(
            PROTECTED_PATTERN.pattern + r"|(?:" + "|".join(term_patterns) + r")",
            PROTECTED_PATTERN.flags,
        )
    protected = pattern.sub(replace, markdown)
    return ProtectedMarkdown(
        protected,
        prefix,
        tuple(replacements),
        tuple(ordered_placeholders),
    )


def restore_markdown(translated: str, protected: ProtectedMarkdown) -> str:
    placeholder_pattern = re.compile(re.escape(protected.prefix) + r"\d{5}__")
    expected = [placeholder for placeholder, _value in protected.replacements]
    observed = placeholder_pattern.findall(translated)
    if Counter(observed) != Counter(expected):
        raise TranslationError("模型修改、遗漏或重复了 Markdown 保护占位符")
    ordered = set(protected.ordered_placeholders)
    if [value for value in observed if value in ordered] != list(
        protected.ordered_placeholders
    ):
        raise TranslationError("模型重排了图片、链接、HTML 或代码块占位符")
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
    if Counter(source_protected) != Counter(target_protected):
        raise TranslationError("最终 Markdown 中的公式、代码、链接、图片或 HTML 结构发生变化")
    source_ordered = [
        value for value in source_protected if not _protected_value_may_reorder(value)
    ]
    target_ordered = [
        value for value in target_protected if not _protected_value_may_reorder(value)
    ]
    if source_ordered != target_ordered:
        raise TranslationError("最终 Markdown 重排了图片、链接、HTML 或代码块")


def preserve_outer_whitespace(source: str, translated: str) -> str:
    """Restore exact chunk-boundary whitespace that models often trim."""
    match = re.fullmatch(r"(\s*)(.*?)(\s*)", source, re.DOTALL)
    assert match is not None
    leading, _core, trailing = match.groups()
    return leading + translated.strip() + trailing


def normalize_accidental_term_code_spans(
    source: str,
    translated: str,
    terms: Sequence[str],
) -> str:
    """Remove code formatting a model added around otherwise plain protected terms."""
    normalized = translated
    for term in terms:
        if not term or "`" in term:
            continue
        pattern = re.compile(r"(?P<fence>`+)" + re.escape(term) + r"(?P=fence)")
        if pattern.search(source) is None:
            normalized = pattern.sub(lambda _match: term, normalized)
    return normalized


def normalize_preserved_url_boundaries(source: str, translated: str) -> str:
    """Keep restored URLs separate from adjacent translated word characters."""
    urls = {
        value
        for _placeholder, value in protect_markdown(source).replacements
        if value.startswith(("http://", "https://", "mailto:"))
    }
    normalized = translated
    for url in sorted(urls, key=len, reverse=True):
        normalized = re.sub(
            re.escape(url) + r"(?=[A-Za-z0-9_\u4e00-\u9fff])",
            lambda _match: url + " ",
            normalized,
        )
    return normalized


def split_failed_translation_chunk(
    markdown: str,
    *,
    minimum_side_chars: int,
) -> Optional[Tuple[str, str]]:
    """Split one failing chunk near its midpoint without cutting protected syntax."""
    midpoint = len(markdown) // 2
    protected_ranges = [match.span() for match in PROTECTED_PATTERN.finditer(markdown)]

    def allowed(position: int) -> bool:
        if position <= 0 or position >= len(markdown):
            return False
        if len(markdown[:position].strip()) < minimum_side_chars:
            return False
        if len(markdown[position:].strip()) < minimum_side_chars:
            return False
        return not any(start < position < end for start, end in protected_ranges)

    candidate_groups = [
        [match.end() for match in re.finditer(r"\n[ \t]*\n", markdown)],
        [match.end() for match in re.finditer(r"(?<=[.!?。！？])\s+", markdown)],
        [match.end() for match in re.finditer(r"\n", markdown)],
        [match.end() for match in re.finditer(r"[ \t]+", markdown)],
    ]
    for candidates in candidate_groups:
        valid = [position for position in candidates if allowed(position)]
        if valid:
            position = min(valid, key=lambda value: abs(value - midpoint))
            return markdown[:position], markdown[position:]
    return None


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
    do_not_translate: str,
) -> str:
    prompt = f"""You are a professional academic translator.
Translate the supplied Markdown fragment from {language_name(source_language)} to {language_name(target_language)}.

Mandatory rules:
1. Return only the translated Markdown fragment. Do not add code fences, explanations, summaries, or commentary.
2. Preserve every placeholder beginning with __MDT_ exactly once. Formula, inline-code, and do-not-translate placeholders may move only when target-language grammar requires it. Never reorder image, link, URL, HTML, or fenced-code placeholders.
3. Preserve Markdown structure, heading levels, list numbering, paragraph order, and line breaks where practical.
4. Translate all natural-language prose faithfully without omission, condensation, expansion, or factual correction.
5. Keep author names, model names, dataset names, citations, variable names, abbreviations, URLs, and numeric values unchanged unless a conventional translated name is unambiguous.
6. Use concise, publication-quality academic language and consistent terminology.
"""
    if glossary:
        prompt += "\nRequired terminology:\n" + glossary + "\n"
    if do_not_translate:
        prompt += (
            "\nDo-not-translate terms:\n"
            "Keep every listed term exactly as written, including capitalization.\n"
            + do_not_translate
            + "\n"
        )
    return prompt


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
    do_not_translate_hash: str,
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
        "do_not_translate_hash": do_not_translate_hash,
        "thinking": thinking,
    }
    if processor_version:
        payload["processor_version"] = processor_version
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def add_usage(total: Dict[str, int], current: Dict[str, int]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = int(total.get(key, 0) or 0) + int(current.get(key, 0) or 0)


def local_asset_references(
    markdown: str, source_dir: Path
) -> List[Tuple[str, Path, Path]]:
    raw_paths: List[str] = []
    raw_paths.extend(
        match.group("destination").strip("<>")
        for match in MARKDOWN_IMAGE_RE.finditer(markdown)
    )
    raw_paths.extend(
        match.group("destination") for match in HTML_IMAGE_RE.finditer(markdown)
    )

    source_root = source_dir.resolve()
    result: List[Tuple[str, Path, Path]] = []
    seen: set[str] = set()
    for raw in raw_paths:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme or parsed.netloc or raw.startswith(("#", "/")):
            continue
        relative_text = urllib.parse.unquote(parsed.path)
        if not relative_text or raw in seen:
            continue
        candidate = (source_dir / relative_text).resolve()
        try:
            relative = candidate.relative_to(source_root)
        except ValueError:
            eprint(f"警告：跳过源目录之外的资源：{raw}")
            continue
        if candidate.is_file():
            seen.add(raw)
            result.append((raw, candidate, relative))
        else:
            eprint(f"警告：Markdown 引用的本地资源不存在：{raw}")
    return result


def local_asset_paths(markdown: str, source_dir: Path) -> List[Tuple[Path, Path]]:
    result: List[Tuple[Path, Path]] = []
    seen: set[Path] = set()
    for _raw, candidate, relative in local_asset_references(markdown, source_dir):
        if candidate not in seen:
            seen.add(candidate)
            result.append((candidate, relative))
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


def rewrite_shared_asset_paths(
    markdown: str, source: Path, output_path: Path
) -> Tuple[str, int]:
    """Point translated image links at the source assets without copying them."""
    replacements: Dict[str, str] = {}
    assets: set[Path] = set()
    output_dir = output_path.parent.resolve()
    for raw, asset, _relative in local_asset_references(markdown, source.parent):
        parsed = urllib.parse.urlsplit(raw)
        relative = Path(os.path.relpath(asset, output_dir)).as_posix()
        encoded_relative = urllib.parse.quote(relative, safe="/:@-._~")
        replacements[raw] = urllib.parse.urlunsplit(
            ("", "", encoded_relative, parsed.query, parsed.fragment)
        )
        assets.add(asset)

    def replace_markdown_image(match: re.Match[str]) -> str:
        destination = match.group("destination")
        wrapped = destination.startswith("<") and destination.endswith(">")
        raw = destination[1:-1] if wrapped else destination
        replacement = replacements.get(raw)
        if replacement is None:
            return match.group(0)
        if wrapped:
            replacement = f"<{replacement}>"
        return f"{match.group(1)}{replacement}{match.group('tail')}"

    def replace_html_image(match: re.Match[str]) -> str:
        replacement = replacements.get(match.group("destination"))
        if replacement is None:
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('prefix')}{quote}{replacement}{quote}"

    rewritten = MARKDOWN_IMAGE_RE.sub(replace_markdown_image, markdown)
    rewritten = HTML_IMAGE_RE.sub(replace_html_image, rewritten)

    for replacement in replacements.values():
        path = urllib.parse.unquote(urllib.parse.urlsplit(replacement).path)
        if not (output_dir / path).resolve().is_file():
            raise TranslationError(f"重写后的本地资源无法解析：{replacement}")
    return rewritten, len(assets)


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
    parser.add_argument(
        "--do-not-translate-file",
        type=Path,
        default=DEFAULT_DO_NOT_TRANSLATE_FILE,
        help=f"不翻译词汇表，默认 {DEFAULT_DO_NOT_TRANSLATE_FILE}",
    )
    parser.add_argument(
        "--table-mode",
        choices=["auto", "image", "html"],
        default="auto",
        help="表格输出：auto 优先使用 MinerU 原始截图，image 强制截图，html 保留可搜索 HTML",
    )
    parser.add_argument(
        "--content-list",
        type=Path,
        help="MinerU *_content_list.json；默认在输入 Markdown 同目录自动发现",
    )
    parser.add_argument("--max-chars", type=int, default=12000, help="每个翻译块的近似字符上限")
    parser.add_argument(
        "--structure-retries",
        type=int,
        default=3,
        help="单块结构失败后的尝试次数；耗尽后自动拆分失败块，默认 3",
    )
    parser.add_argument(
        "--adaptive-min-chars",
        type=int,
        default=400,
        help="自动拆分失败块时每侧保留的最少字符数，默认 400",
    )
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
    asset_group = parser.add_mutually_exclusive_group()
    asset_group.add_argument(
        "--copy-assets",
        action="store_true",
        help="把 Markdown 引用的本地资源复制到翻译目录，生成可独立移动的副本",
    )
    asset_group.add_argument(
        "--no-copy-assets",
        action="store_false",
        dest="copy_assets",
        help="兼容选项；不复制资源并重写链接（当前默认行为）",
    )
    parser.set_defaults(copy_assets=False)
    parser.add_argument("--force", action="store_true", help="忽略已有翻译缓存并重新调用 API")
    parser.add_argument("--yes", action="store_true", help="确认把 Markdown 文本发送到云端翻译 API")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.structure_retries < 1:
            raise TranslationError("--structure-retries 不能小于 1")
        if args.adaptive_min_chars < 100:
            raise TranslationError("--adaptive-min-chars 不能小于 100")
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
        do_not_translate, do_not_translate_terms, do_not_translate_hash = (
            load_do_not_translate(args.do_not_translate_file)
        )
        table_conversion = replace_html_tables_with_images(
            markdown,
            source,
            table_mode=args.table_mode,
            content_list_path=args.content_list,
        )
        translation_source = table_conversion.markdown
        parts = document_parts(
            translation_source,
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
                "translation_source_sha256": sha256_text(translation_source),
                "target_language": args.target_language,
                "model": model,
                "base_url": args.base_url,
                "table_mode": table_conversion.mode,
                "content_list_path": (
                    str(table_conversion.content_list_path)
                    if table_conversion.content_list_path
                    else None
                ),
            }
        )
        state.setdefault("chunks", {})
        state.setdefault("usage", {})
        prompt = build_system_prompt(
            source_language=args.source_language,
            target_language=args.target_language,
            glossary=glossary,
            do_not_translate=do_not_translate,
        )

        output_parts: List[str] = []
        translated_index = 0
        api_calls = 0
        cache_hits = 0
        adaptive_splits = 0

        def key_for(text: str) -> str:
            return cache_key(
                text,
                base_url=args.base_url,
                model=model,
                source_language=args.source_language,
                target_language=args.target_language,
                glossary_hash=glossary_hash,
                do_not_translate_hash=do_not_translate_hash,
                thinking=args.thinking,
                processor_version="table-images-v1-do-not-translate",
            )

        def translate_chunk(text: str, label: str) -> str:
            nonlocal api_calls, cache_hits, adaptive_splits

            def clean_and_validate(value: str) -> str:
                value = preserve_outer_whitespace(text, value)
                value = normalize_accidental_term_code_spans(
                    text,
                    value,
                    do_not_translate_terms,
                )
                value = normalize_preserved_url_boundaries(text, value)
                validate_final_document(text, value)
                return value

            key = key_for(text)
            cached = state["chunks"].get(key)
            if isinstance(cached, dict) and isinstance(cached.get("translation"), str):
                try:
                    translated = clean_and_validate(cached["translation"])
                except TranslationError:
                    eprint(f"翻译块 {label} 的旧缓存未通过新校验，正在重新翻译")
                    state["chunks"].pop(key, None)
                else:
                    cache_hits += 1
                    eprint(f"复用翻译块 {label}")
                    return translated

            eprint(f"正在翻译块 {label}…")
            protected = protect_markdown(text, do_not_translate_terms)
            last_structure_error: Optional[TranslationError] = None
            for structure_attempt in range(1, args.structure_retries + 1):
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
                    translated = clean_and_validate(translated)
                    break
                except TranslationError as exc:
                    last_structure_error = exc
                    if structure_attempt < args.structure_retries:
                        eprint(f"翻译块 {label} 结构异常，正在重试…")
            else:
                split = split_failed_translation_chunk(
                    text,
                    minimum_side_chars=args.adaptive_min_chars,
                )
                if split is None:
                    assert last_structure_error is not None
                    raise last_structure_error
                adaptive_splits += 1
                eprint(f"翻译块 {label} 持续结构异常，自动拆分为两个子块")
                left, right = split
                translated = translate_chunk(left, label + ".1") + translate_chunk(
                    right, label + ".2"
                )
                translated = clean_and_validate(translated)

            translated = clean_and_validate(translated)
            state["chunks"][key] = {
                "source_sha256": sha256_text(text),
                "translation": translated,
            }
            save_state(state_path, state)
            return translated

        for part in chunks:
            if not part.translatable:
                output_parts.append(part.text)
                continue
            translated_index += 1
            output_parts.append(
                translate_chunk(part.text, f"{translated_index}/{translatable_count}")
            )

        translated_markdown = normalize_accidental_term_code_spans(
            translation_source,
            "".join(output_parts),
            do_not_translate_terms,
        )
        translated_markdown = normalize_preserved_url_boundaries(
            translation_source,
            translated_markdown,
        )
        if not translated_markdown.strip():
            raise TranslationError("翻译结果为空")
        validate_final_document(translation_source, translated_markdown)
        if args.copy_assets:
            output_markdown = translated_markdown
            copied_assets = copy_assets(translated_markdown, source, output_dir)
            shared_assets = 0
        else:
            output_markdown, shared_assets = rewrite_shared_asset_paths(
                translated_markdown, source, output_path
            )
            copied_assets = 0
        atomic_write_text(output_path, output_markdown)
        state["completed"] = True
        state["output_path"] = str(output_path.resolve())
        state["asset_mode"] = "copied" if args.copy_assets else "shared"
        save_state(state_path, state)

        result = {
            "source_path": str(source),
            "output_dir": str(output_dir.resolve()),
            "markdown_path": str(output_path.resolve()),
            "state_path": str(state_path.resolve()),
            "model": model,
            "target_language": args.target_language,
            "table_mode": table_conversion.mode,
            "table_images": table_conversion.image_count,
            "content_list_path": (
                str(table_conversion.content_list_path)
                if table_conversion.content_list_path
                else None
            ),
            "do_not_translate_file": str(args.do_not_translate_file.expanduser().resolve()),
            "do_not_translate_terms": len(do_not_translate_terms),
            "thinking": args.thinking,
            "translation_chunks": translatable_count,
            "adaptive_splits": adaptive_splits,
            "api_calls": api_calls,
            "cache_hits": cache_hits,
            "asset_mode": state["asset_mode"],
            "copied_assets": copied_assets,
            "shared_assets": shared_assets,
            "usage": state.get("usage", {}),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (TranslationError, OSError, UnicodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
