#!/usr/bin/env python3
"""Normalize MinerU-specific Markdown fragments into portable Markdown."""

from __future__ import annotations

import html
import re
from typing import Match, Tuple


ALGORITHM_DIV_PATTERN = re.compile(
    r"<div\b(?P<attrs>[^>]*)>(?P<body>.*?)</div\s*>",
    re.IGNORECASE | re.DOTALL,
)
ALGORITHM_CLASS_PATTERN = re.compile(
    r"\bclass\s*=\s*(?:\"[^\"]*\bmineru-algorithm\b[^\"]*\"|"
    r"'[^']*\bmineru-algorithm\b[^']*')",
    re.IGNORECASE,
)
KNOWN_INLINE_TAG_PATTERN = re.compile(
    r"</?(?:span|em|strong|b|i|code|kbd|samp|var)\b[^>]*>",
    re.IGNORECASE,
)


LATEX_COMMANDS = {
    "Longleftrightarrow": "⇔",
    "Leftrightarrow": "⇔",
    "Longrightarrow": "⇒",
    "Longleftarrow": "⇐",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "rightarrow": "→",
    "leftarrow": "←",
    "gets": "←",
    "to": "→",
    "setminus": "∖",
    "varnothing": "∅",
    "emptyset": "∅",
    "triangleq": "≜",
    "approx": "≈",
    "propto": "∝",
    "notin": "∉",
    "subseteq": "⊆",
    "supseteq": "⊇",
    "subset": "⊂",
    "supset": "⊃",
    "bigcup": "⋃",
    "bigcap": "⋂",
    "cup": "∪",
    "cap": "∩",
    "geq": "≥",
    "leq": "≤",
    "neq": "≠",
    "ge": "≥",
    "le": "≤",
    "ne": "≠",
    "equiv": "≡",
    "land": "∧",
    "lor": "∨",
    "wedge": "∧",
    "vee": "∨",
    "neg": "¬",
    "times": "×",
    "cdot": "·",
    "odot": "⊙",
    "oplus": "⊕",
    "pm": "±",
    "parallel": "∥",
    "perp": "⊥",
    "mid": "|",
    "infty": "∞",
    "ldots": "…",
    "cdots": "…",
    "dots": "…",
    "sum": "Σ",
    "prod": "Π",
    "forall": "∀",
    "exists": "∃",
    "in": "∈",
    "sim": "∼",
    "Delta": "Δ",
    "delta": "δ",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "lambda": "λ",
    "theta": "θ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "sigma": "σ",
    "tau": "τ",
    "omega": "ω",
    "phi": "φ",
    "psi": "ψ",
    "pi": "π",
    "rho": "ρ",
    "eta": "η",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "zeta": "ζ",
    "ell": "ℓ",
    "arg": "arg",
    "max": "max",
    "min": "min",
    "log": "log",
    "exp": "exp",
    "len": "len",
}
FORMATTING_COMMANDS = (
    "mathbf",
    "mathrm",
    "mathcal",
    "mathbb",
    "mathsf",
    "mathtt",
    "text",
    "textrm",
    "texttt",
    "textsf",
    "textnormal",
    "textbf",
    "operatorname",
    "emph",
)
ACCENT_COMMANDS = ("hat", "widehat", "tilde", "widetilde", "bar", "vec")


def _strip_known_html(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    fragment = re.sub(
        r"<sup\b[^>]*>(.*?)</sup\s*>",
        lambda match: f"^({match.group(1)})",
        fragment,
        flags=re.IGNORECASE | re.DOTALL,
    )
    fragment = re.sub(
        r"<sub\b[^>]*>(.*?)</sub\s*>",
        lambda match: f"_({match.group(1)})",
        fragment,
        flags=re.IGNORECASE | re.DOTALL,
    )
    fragment = re.sub(r"</?p\b[^>]*>", "\n", fragment, flags=re.IGNORECASE)
    return KNOWN_INLINE_TAG_PATTERN.sub("", fragment)


def _unescape_html_entities(value: str) -> str:
    for _ in range(3):
        decoded = html.unescape(value)
        if decoded == value:
            break
        value = decoded
    return value.replace("\xa0", " ")


def _replace_latex_commands(value: str) -> str:
    for command in sorted(LATEX_COMMANDS, key=len, reverse=True):
        value = re.sub(
            rf"\\{command}(?![A-Za-z])",
            LATEX_COMMANDS[command],
            value,
        )
    return value


def _unwrap_latex_groups(value: str) -> str:
    formatting = "|".join(FORMATTING_COMMANDS)
    accents = "|".join(ACCENT_COMMANDS)
    for _ in range(8):
        updated = re.sub(
            rf"\\(?:{formatting})\s*\{{([^{{}}]*)\}}",
            r"\1",
            value,
        )
        updated = re.sub(
            rf"\\({accents})\s*\{{([^{{}}]*)\}}",
            lambda match: f"{match.group(1)}({match.group(2)})",
            updated,
        )
        updated = re.sub(
            r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
            lambda match: f"({match.group(1)}) / ({match.group(2)})",
            updated,
        )
        updated = re.sub(
            r"\\sqrt\s*\{([^{}]*)\}",
            lambda match: f"sqrt({match.group(1)})",
            updated,
        )
        if updated == value:
            break
        value = updated
    return value


def _plain_pseudocode_line(line: str) -> str:
    leading = re.match(r"^[ \t]*", line).group(0).replace("\t", "    ")
    value = line[len(re.match(r"^[ \t]*", line).group(0)) :].strip()
    if not value:
        return ""

    value = re.sub(r"(?<!\\)\$+", "", value)
    value = re.sub(r"\\(?:left|right)(?![A-Za-z])", "", value)
    value = re.sub(r"\\(?:quad|qquad|,|;|:|!)(?![A-Za-z])", " ", value)
    value = _unwrap_latex_groups(value)
    value = _replace_latex_commands(value)
    value = _unwrap_latex_groups(value)
    value = re.sub(
        r"_\{([^{}]+)\}",
        lambda match: "_"
        + (
            match.group(1)
            if re.fullmatch(r"[A-Za-z0-9]+", match.group(1))
            else f"({match.group(1)})"
        ),
        value,
    )
    value = re.sub(
        r"\^\{([^{}]+)\}",
        lambda match: "^" + (
            match.group(1) if len(match.group(1)) == 1 else f"({match.group(1)})"
        ),
        value,
    )
    value = re.sub(r"\\([_{}%&#])", r"\1", value)
    value = re.sub(r"\\([A-Za-z]+)", r"\1", value)
    value = re.sub(r"\\(.)", r"\1", value)
    value = re.sub(r"[ \t]+", " ", value).strip()
    value = re.sub(r"<\s+([^<>]+?)\s+>", r"<\1>", value)
    value = re.sub(r"([A-Za-z0-9_])\s+\(", r"\1(", value)
    value = re.sub(r"\(\s+", "(", value)
    value = re.sub(r"\s+\)", ")", value)
    value = re.sub(r"\s+([,.;])", r"\1", value)
    return leading + value


def _indent_numbered_pseudocode(lines: list[str]) -> list[str]:
    """Recover common control-flow indentation when MinerU flattened it."""
    result = []
    level = 0
    for line in lines:
        match = re.match(r"^\s*(\d+)\s*[:.]?\s+(.*)$", line)
        if not match:
            result.append(line)
            continue
        number, statement = match.groups()
        normalized = statement.strip()
        lowered = normalized.lower()
        closes = bool(
            re.match(r"(?:end\s+(?:if|for|while)|else\b|elif\b|until\b)", lowered)
        )
        if closes:
            level = max(0, level - 1)
        result.append(f"{number}: " + "    " * level + normalized)
        opens = bool(
            re.match(r"(?:if\b.*\bthen\b|for(?:each)?\b.*\bdo\b|while\b.*\bdo\b)", lowered)
            or re.match(r"(?:else|elif)\b", lowered)
        )
        if opens and not lowered.startswith("end "):
            level += 1
    return result


def clean_algorithm_body(fragment: str) -> str:
    """Turn MinerU's algorithm HTML body into readable plain pseudocode."""
    text = _unescape_html_entities(_strip_known_html(fragment))
    lines = [_plain_pseudocode_line(line) for line in text.splitlines()]
    nonempty_count = sum(bool(line) for line in lines)
    blank_count = len(lines) - nonempty_count
    if blank_count >= max(2, nonempty_count // 2):
        lines = [line for line in lines if line]
    lines = _indent_numbered_pseudocode(lines)
    compact = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    while compact and not compact[-1]:
        compact.pop()
    return "\n".join(compact).strip()


def _render_algorithm(match: Match[str]) -> str:
    if not ALGORITHM_CLASS_PATTERN.search(match.group("attrs")):
        return match.group(0)

    cleaned = clean_algorithm_body(match.group("body"))
    if not cleaned:
        return match.group(0)
    lines = cleaned.splitlines()
    title = ""
    if lines and re.match(r"^(?:algorithm\b|算法\b)", lines[0], re.IGNORECASE):
        title = lines.pop(0).strip()
        while lines and not lines[0]:
            lines.pop(0)
    pseudocode = "\n".join(lines).strip() if title else cleaned
    if not pseudocode:
        pseudocode = title
        title = ""

    fenced = f"```text\n{pseudocode}\n```"
    return f"**{title}**\n\n{fenced}" if title else fenced


def normalize_algorithm_blocks(markdown: str) -> Tuple[str, int]:
    """Replace MinerU algorithm divs with portable fenced pseudocode blocks."""
    count = 0

    def replace(match: Match[str]) -> str:
        nonlocal count
        rendered = _render_algorithm(match)
        if rendered != match.group(0):
            count += 1
        return rendered

    return ALGORITHM_DIV_PATTERN.sub(replace, markdown), count
