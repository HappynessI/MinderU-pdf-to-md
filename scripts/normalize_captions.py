#!/usr/bin/env python3
"""Normalize figure/table caption openers in a translated Markdown file to `图N：` / `表N：`.

Two classes, because the same shape means different things in the two positions:

* `图/表 + number + explicit separator` (`.．。:：`) is a caption wherever the line sits.
* `图/表 + space + number` is a caption only when the previous non-empty line is an image
  reference or an `(a)`/`(b)` panel label. A body sentence that opens with a table number
  (`表 3 展示了…`) keeps its spacing — the knowledge base's citation style for an in-text
  reference is `表3 中…` inside a sentence, not a caption opener.

A panel label may carry text after the bracket (`(c) UpperNet在ADE20K上。`), so the label
pattern must not anchor at end of line; anchoring there misses the captions that follow a
labelled panel row.

Run it on `translation-zh-CN/full-CN.md` only — `full.md` stays the source of record.
Idempotent: a line already shaped `图N：…` is left alone.

Usage: python3 normalize_captions.py full-CN.md [more.md ...]
"""

import re
import sys
from pathlib import Path

IMGREF = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$")
PANEL_LABEL = re.compile(r"^\s*[（(][a-z][）)]")
STRICT = re.compile(r"^\s*(图|表)\s*([0-9]+)\s*[.．。:：]\s*")
LOOSE = re.compile(r"^\s*(图|表)\s*([0-9]+)\s+")


def normalize(path: Path, verbose: bool = True) -> int:
    lines = path.read_text(encoding="utf-8").split("\n")
    in_context = False
    count = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if IMGREF.match(line) or PANEL_LABEL.match(line):
            in_context = True
            continue
        match = STRICT.match(line) or (LOOSE.match(line) if in_context else None)
        if match:
            new = f"{match.group(1)}{match.group(2)}：{line[match.end():]}"
            if new != line:
                lines[i] = new
                count += 1
        in_context = False
    if count:
        path.write_text("\n".join(lines), encoding="utf-8")
    if verbose:
        print(f"{path}: normalized {count} caption line(s)")
    return count


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    for arg in argv[1:]:
        normalize(Path(arg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
