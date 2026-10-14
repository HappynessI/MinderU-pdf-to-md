#!/usr/bin/env python3
"""Rebuild MinerU-split multi-panel figures as one image, cropped from the original PDF.

MinerU emits one visual figure as several adjacent `chart` blocks when the figure has
multiple panels. This script detects those groups from `<uuid>_content_list.json`,
crops the whole panel region out of the original PDF page at high resolution, saves it
as `images/figure-NN.jpg`, and rewrites the Markdown (source and translation, if given)
so each figure carries exactly one image reference and one caption.

Panels are detected, not guessed. In the content list the closing block carries
`Figure N: ...` (VLM output uses a colon as often as a period), each earlier panel has
either an empty caption or only its own `(a)`/`(b)` panel label. Captions are read from
the block-type-specific key (`chart_caption`, `image_caption`, `table_caption`): MinerU
sometimes types one panel of a figure as a `table` block while keeping the figure
caption, so that block is treated as a panel. A table block carrying a `Table N` caption —
or no caption at all — is never absorbed, so a real table image sitting above a figure
stays out of the crop.

Usage:
  python3 merge_figure_panels.py PAPER.pdf --mineru-dir DIR \
      [--markdown full.md] [--translation translation-zh-CN/full-CN.md] \
      [--dpi 300] [--pad 5] [--dry-run]

Requirements: pymupdf (`uv venv .pdfenv && uv pip install --python .pdfenv/bin/python pymupdf`).
Groups whose panels do not all sit on one PDF page are reported and left untouched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

IMGREF = re.compile(r'!\[\]\((?:\.\./)?images/([0-9a-f]+\.jpg)\)')
CAPTION = re.compile(r'^\s*(?:Figure|图)\s*(\d+)\s*[.．:：。]')
PANEL_LABEL = re.compile(r'^\s*[（(][a-z][）)]')
CAPTION_KEY = {'chart': 'chart_caption', 'image': 'image_caption', 'table': 'table_caption'}


def load_content_list(mineru_dir: Path) -> list[dict]:
    cands = sorted(mineru_dir.glob('*_content_list.json'))
    if not cands:
        raise SystemExit(f'no *_content_list.json under {mineru_dir}')
    return json.loads(cands[0].read_text(encoding='utf-8'))


def captions_of(item: dict) -> list[str]:
    """Caption strings of a content-list block, read from its type-specific key."""
    key = CAPTION_KEY.get(item.get('type'))
    if not key:
        return []
    cap = item.get(key)
    if isinstance(cap, list):
        caps = cap
    elif isinstance(cap, str) and cap.strip():
        try:
            caps = json.loads(cap)
        except json.JSONDecodeError:
            caps = [cap]
    else:
        caps = []
    return [c for c in caps if isinstance(c, str) and c.strip()]


def figure_caption(caps: list[str]):
    """Return the match of the `Figure N` caption among these caption strings."""
    for c in caps:
        m = CAPTION.match(c)
        if m:
            return m
    return None


def find_groups(items: list[dict]) -> list[dict]:
    """Return [{figure:int, panels:[item,...]}] for every split multi-panel figure."""
    groups = []
    for i, item in enumerate(items):
        if item.get('type') not in CAPTION_KEY:
            continue
        m = figure_caption(captions_of(item))
        if not m:
            continue
        run = [item]
        j = i - 1
        while j >= 0:
            prev = items[j]
            if prev.get('page_idx') != item.get('page_idx'):
                break
            if prev.get('type') not in ('chart', 'image'):
                # Only chart/image blocks precede a figure as panels; a `table` block
                # carries its own caption, so absorbing one would swallow a real table.
                break
            # A panel may carry its own (a)/(b) label; only a real figure caption
            # closes the run.
            pcaps = [c for c in captions_of(prev) if not PANEL_LABEL.match(c)]
            if pcaps:
                break
            run.insert(0, prev)
            j -= 1
        if len(run) > 1:
            groups.append({'figure': int(m.group(1)), 'panels': run})
    return groups


def text_line_rects(pymupdf, page) -> list[tuple]:
    """Text-line rectangles of one page, read from the PDF's own text layer.

    Line granularity, not paragraph: a crop edge that lands inside a line is moved to
    that line's near side, and the move is bounded by one line's height. A page without
    a text layer (scanned PDF converted with --ocr) yields no rects and no clamping.
    """
    rects = []
    try:
        data = page.get_text('dict')
    except Exception:
        return rects
    for block in data.get('blocks', []):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            bbox = line.get('bbox')
            if not bbox:
                continue
            spans = line.get('spans', [])
            if not ''.join(s.get('text', '') for s in spans).strip():
                continue
            rects.append(tuple(float(v) for v in bbox))
    return rects


EDGE_MARGIN = 0.5


def clamp_edges(edges, union, text_rects, margin: float = EDGE_MARGIN,
                passes: int = 4) -> tuple:
    """Pull a padded crop box off any line of text its edges would slice in half.

    `edges` is the padded box `(x0, y0, x1, y1)` on the page's point grid, `union` the
    panel-bbox union: padding may shrink to zero, never grow. A line crossing an edge is
    excluded by moving that edge to the line's near side, so the reader never sees half a
    glyph row. A line that sits inside `union` cannot be excluded and is left alone.
    """
    x0, y0, x1, y1 = edges
    ux0, uy0, ux1, uy1 = union
    for _ in range(passes):
        moved = False
        for rx0, ry0, rx1, ry1 in text_rects:
            if rx1 <= x0 or rx0 >= x1:
                continue
            if ry0 < y1 < ry1:
                new = max(uy1, min(y1, ry0 - margin))
                if new < y1 - 1e-9:
                    y1, moved = new, True
                    continue
            if ry0 < y0 < ry1:
                new = min(uy0, max(y0, ry1 + margin))
                if new > y0 + 1e-9:
                    y0, moved = new, True
                    continue
        for rx0, ry0, rx1, ry1 in text_rects:
            if ry1 <= y0 or ry0 >= y1:
                continue
            if rx0 < x0 < rx1:
                new = min(ux0, max(x0, rx1 + margin))
                if new > x0 + 1e-9:
                    x0, moved = new, True
                    continue
            if rx0 < x1 < rx1:
                new = max(ux1, min(x1, rx0 - margin))
                if new < x1 - 1e-9:
                    x1, moved = new, True
                    continue
        if not moved:
            break
    return (x0, y0, x1, y1)


def crop_rect(pymupdf, page, panels: list[dict], pad: float, clamp_text: bool = True):
    """Union of the panel bboxes, on the page's point grid, grown by pad.

    The padded box is then clamped off any text line it would slice (see `clamp_edges`).
    """
    sx, sy = page.rect.width / 1000.0, page.rect.height / 1000.0
    xs0 = [p['bbox'][0] * sx for p in panels]
    ys0 = [p['bbox'][1] * sy for p in panels]
    xs1 = [p['bbox'][2] * sx for p in panels]
    ys1 = [p['bbox'][3] * sy for p in panels]
    union = (min(xs0), min(ys0), max(xs1), max(ys1))
    box = (union[0] - pad, union[1] - pad, union[2] + pad, union[3] + pad)
    if clamp_text:
        box = clamp_edges(box, union, text_line_rects(pymupdf, page))
    return pymupdf.Rect(*box)


def crop_group(doc, group: dict, out_dir: Path, dpi: int, pad: float) -> tuple[str, str]:
    panels = group['panels']
    page_idx = panels[0]['page_idx']
    page = doc[page_idx]
    import pymupdf

    rect = crop_rect(pymupdf, page, panels, pad)
    z = dpi / 72.0
    pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csRGB)
    name = f'figure-{group["figure"]:02d}.jpg'
    out = out_dir / name
    pix.save(str(out), jpg_quality=92)
    return name, f'{pix.width}x{pix.height}'


def rewrite_markdown(path: Path, group: dict, new_name: str, prefix: str) -> str:
    text = path.read_text(encoding='utf-8')
    hashes = [Path(p['img_path']).name for p in group['panels']]
    spans = []
    for h in hashes:
        m = re.search(r'!\[\]\((?:\.\./)?images/' + re.escape(h) + r'\)', text)
        if not m:
            return 'skip: already merged or refs not found'
        spans.append(m)
    spans.sort(key=lambda m: m.start())
    span = text[spans[0].start():spans[-1].end()]
    leftovers = IMGREF.sub('', span)
    panels = [ln.strip() for ln in leftovers.split('\n') if ln.strip()]
    bad = [ln for ln in panels if not PANEL_LABEL.match(ln)]
    if bad:
        return f'skip: unexpected content between panels ({bad[0][:60]!r})'
    found = set(IMGREF.findall(span))
    if found != set(hashes):
        return 'skip: panel refs are not contiguous'
    new = f'![]({prefix}images/{new_name})\n'
    if panels:
        new += '\n'.join(panels) + '\n'
    text = text[:spans[0].start()] + new + text[spans[-1].end():]
    path.write_text(text, encoding='utf-8')
    return f'merged {len(hashes)} panels -> images/{new_name}'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--mineru-dir', required=True)
    ap.add_argument('--markdown', default='full.md')
    ap.add_argument('--translation', default=None,
                    help='e.g. translation-zh-CN/full-CN.md')
    ap.add_argument('--images-subdir', default='images')
    ap.add_argument('--dpi', type=int, default=300)
    ap.add_argument('--pad', type=float, default=5.0)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    try:
        import pymupdf
    except ImportError:
        raise SystemExit('pymupdf missing: uv venv .pdfenv && '
                         'uv pip install --python .pdfenv/bin/python pymupdf')

    mineru = Path(args.mineru_dir)
    images_dir = mineru / args.images_subdir
    items = load_content_list(mineru)
    groups = find_groups(items)
    if not groups:
        print('no split multi-panel figures found')
        return 0

    doc = pymupdf.open(args.pdf)
    report = []
    for g in groups:
        pages = {p['page_idx'] for p in g['panels']}
        if len(pages) > 1:
            report.append({'figure': g['figure'], 'status':
                           f'skip: panels span pages {sorted(pages)} - crop manually'})
            continue
        name, size = crop_group(doc, g, images_dir, args.dpi, args.pad)
        entry = {'figure': g['figure'], 'panels': len(g['panels']),
                 'image': f'{args.images_subdir}/{name}', 'size': size}
        if args.dry_run:
            entry['status'] = 'dry-run'
        else:
            results = []
            for rel, prefix in [(args.markdown, ''), (args.translation, '../')]:
                if not rel:
                    continue
                p = mineru / rel
                if not p.exists():
                    entry[str(rel)] = 'missing file'
                    continue
                res = rewrite_markdown(p, g, name, prefix)
                entry[str(rel)] = res
                results.append(res)
            # A rejected group leaves no Markdown reference to the crop it just wrote:
            # drop that orphan, or the contact sheet shows a figure/table mashup that
            # was never wired into the document.
            if results and all(r.startswith('skip:') and 'already merged' not in r
                               for r in results):
                (images_dir / name).unlink(missing_ok=True)
                entry['status'] = (f'{args.images_subdir}/{name} removed '
                                   '- crop is unreferenced, group rejected')
        report.append(entry)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('\nVerify every rebuilt figure visually before reporting the conversion as complete.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
