#!/usr/bin/env python3
"""Rebuild MinerU-split multi-panel figures as one image, cropped from the original PDF.

MinerU emits one visual figure as several adjacent `chart` blocks when the figure has
multiple panels. This script detects those groups from `<uuid>_content_list.json`,
crops the whole panel region out of the original PDF page at high resolution, saves it
as `images/figure-NN.jpg`, and rewrites the Markdown (source and translation, if given)
so each figure carries exactly one image reference and one caption.

Panels are detected, not guessed: in the content list every panel but the last has an
empty `chart_caption`, and the last one carries `Figure N. ...`. Tables are a different
block type (`table`), so a table image sitting above a figure is never absorbed.

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
CAPTION = re.compile(r'^\s*(?:Figure|图)\s*(\d+)[.。]')
PANEL_LABEL = re.compile(r'^\([a-z]\)')


def load_content_list(mineru_dir: Path) -> list[dict]:
    cands = sorted(mineru_dir.glob('*_content_list.json'))
    if not cands:
        raise SystemExit(f'no *_content_list.json under {mineru_dir}')
    return json.loads(cands[0].read_text(encoding='utf-8'))


def find_groups(items: list[dict]) -> list[dict]:
    """Return [{figure:int, panels:[item,...]}] for every split multi-panel figure."""
    groups = []
    for i, item in enumerate(items):
        if item.get('type') not in ('chart', 'image'):
            continue
        cap = (item.get('chart_caption') or '[]')
        try:
            caps = json.loads(cap) if isinstance(cap, str) else cap
        except json.JSONDecodeError:
            caps = []
        if not caps:
            continue
        m = None
        for c in caps:
            mm = re.match(r'\s*Figure\s+(\d+)[.]', c)
            if mm:
                m = mm
                break
        if not m:
            continue
        run = [item]
        j = i - 1
        while j >= 0:
            prev = items[j]
            if prev.get('type') not in ('chart', 'image'):
                break
            if prev.get('page_idx') != item.get('page_idx'):
                break
            pc = prev.get('chart_caption') or '[]'
            try:
                pcaps = json.loads(pc) if isinstance(pc, str) else pc
            except json.JSONDecodeError:
                pcaps = []
            # A panel may carry its own (a)/(b) label; only a real figure caption
            # closes the run.
            pcaps = [c for c in pcaps if not PANEL_LABEL.match(c.strip())]
            if pcaps:
                break
            run.insert(0, prev)
            j -= 1
        if len(run) > 1:
            groups.append({'figure': int(m.group(1)), 'panels': run})
    return groups


def crop_group(doc, group: dict, out_dir: Path, dpi: int, pad: float) -> tuple[str, str]:
    panels = group['panels']
    page_idx = panels[0]['page_idx']
    page = doc[page_idx]
    scale_x = page.rect.width / 1000.0
    scale_y = page.rect.height / 1000.0
    xs0, ys0, xs1, ys1 = [], [], [], []
    for p in panels:
        b = p['bbox']
        xs0.append(b[0] * scale_x)
        ys0.append(b[1] * scale_y)
        xs1.append(b[2] * scale_x)
        ys1.append(b[3] * scale_y)
    import pymupdf

    rect = pymupdf.Rect(min(xs0) - pad, min(ys0) - pad, max(xs1) + pad, max(ys1) + pad)
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
            for rel, prefix in [(args.markdown, ''), (args.translation, '../')]:
                if not rel:
                    continue
                p = mineru / rel
                if not p.exists():
                    entry[str(rel)] = 'missing file'
                    continue
                entry[str(rel)] = rewrite_markdown(p, g, name, prefix)
        report.append(entry)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('\nVerify every rebuilt figure visually before reporting the conversion as complete.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
