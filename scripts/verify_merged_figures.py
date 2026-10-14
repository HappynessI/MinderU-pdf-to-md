#!/usr/bin/env python3
"""Verify rebuilt multi-panel figures: geometry, text overlap, Markdown wiring, contact sheet.

Complements merge_figure_panels.py: that script builds one crop per multi-panel figure,
this one proves the crop and its wiring before a conversion is reported as complete.

Checks, per group detected in `<uuid>_content_list.json`:
  1. `images/figure-NN.jpg` exists, is referenced exactly once in each Markdown file given,
     and none of the original per-panel references survive;
  2. the saved crop's pixel size equals the panel-bbox union at the conversion dpi
     (PyMuPDF rounds the clip outward, so up to `--px-tolerance` pixels more is rounding,
     not a stale crop);
  3. the crop rectangle does not overlap a text-bearing block on its page — an overlap
     means body text, a caption or a table body was swallowed into the image;
  4. every image reference in each Markdown file resolves on disk.

It also writes a labeled contact sheet of the merged figures for the visual pass: read it
with `vision_analyze`. The default landing spot is the system temporary directory, so the
paper directory keeps its documented footprint (full.md, images/, *_content_list.json,
translation-zh-CN/).

Usage:
  python3 verify_merged_figures.py PAPER.pdf --mineru-dir OUTPUT_DIR --markdown full.md \
      [--translation translation-zh-CN/full-CN.md] [--images-subdir images] \
      [--dpi 300] [--px-tolerance 3] [--overlap-tolerance 4] [--contact-sheet PATH]

Requirements: pymupdf (`uv venv .pdfenv && uv pip install --python .pdfenv/bin/python pymupdf`).
Exit status 1 when any check fails.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from merge_figure_panels import crop_rect, find_groups, load_content_list  # noqa: E402

TEXTY = ('text', 'header', 'footer', 'page_number', 'page_footnote', 'aside_text')
IMGREF = re.compile(r'!\[\]\((?:[^)]*/)?images/([^)]+)\)')


def _fitz():
    import pymupdf
    return pymupdf


def text_hits(pymupdf, page, items, page_idx, rect, tolerance):
    """Text-bearing blocks of the same page whose area overlaps the crop rect."""
    sx, sy = page.rect.width / 1000.0, page.rect.height / 1000.0
    hits = []
    for it in items:
        if it.get('page_idx') != page_idx or it.get('type') not in TEXTY:
            continue
        body = (it.get('text') or '').strip()
        if not body:
            continue
        b = it['bbox']
        other = pymupdf.Rect(b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy)
        inter = pymupdf.Rect(rect)
        inter.intersect(other)
        if not inter.is_empty and inter.get_area() > tolerance:
            hits.append({'type': it.get('type'), 'area': round(inter.get_area(), 1),
                         'text': body[:60]})
    return hits


def sheet(pymupdf, paths, out_path, width=900.0, gap=18.0, label_h=22.0):
    """Stack the merged figures into one labeled image for the visual pass."""
    entries = []
    for path in paths:
        pix = pymupdf.Pixmap(str(path))
        entries.append((path.name, str(path), pix.width, pix.height,
                        width * pix.height / pix.width))
    total = sum(e[4] + label_h + gap for e in entries)
    doc = pymupdf.open()
    page = doc.new_page(width=width + 2 * gap, height=total + gap)
    y = gap
    for name, full, w, h, scaled in entries:
        page.insert_text((gap + 4, y + 15), f'{name}  ({w}x{h}px)', fontsize=13)
        y += label_h
        box = pymupdf.Rect(gap, y, gap + width, y + scaled)
        page.insert_image(box, filename=full)
        page.draw_rect(box, color=(0.6, 0.6, 0.6), width=0.6)
        y += scaled + gap
    out = Path(out_path)
    page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(str(out))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--mineru-dir', required=True)
    ap.add_argument('--markdown', default='full.md')
    ap.add_argument('--translation', default=None)
    ap.add_argument('--images-subdir', default='images')
    ap.add_argument('--dpi', type=int, default=300)
    ap.add_argument('--pad', type=float, default=5.0)
    ap.add_argument('--px-tolerance', type=int, default=3)
    ap.add_argument('--overlap-tolerance', type=float, default=4.0)
    ap.add_argument('--contact-sheet', default=None)
    args = ap.parse_args()

    pymupdf = _fitz()
    mineru = Path(args.mineru_dir)
    items = load_content_list(mineru)
    groups = find_groups(items)
    doc = pymupdf.open(args.pdf)
    markdowns = [mineru / args.markdown]
    if args.translation:
        markdowns.append(mineru / args.translation)
    texts = {str(p): p.read_text(encoding='utf-8') for p in markdowns if p.exists()}

    report, failures, merged = [], [], []
    for g in groups:
        fig, panels = g['figure'], g['panels']
        name = f'figure-{fig:02d}.jpg'
        image = mineru / args.images_subdir / name
        entry = {'figure': fig, 'panels': len(panels), 'image': f'{args.images_subdir}/{name}'}
        if not image.exists():
            entry['status'] = 'missing image'
            failures.append(f'figure {fig}: {name} does not exist')
            report.append(entry)
            continue
        merged.append(image)

        page = doc[panels[0]['page_idx']]
        rect = crop_rect(pymupdf, page, panels, args.pad)
        pix = pymupdf.Pixmap(str(image))
        expected = (round(rect.width * args.dpi / 72.0), round(rect.height * args.dpi / 72.0))
        entry['px'] = f'{pix.width}x{pix.height}'
        entry['px_expected'] = f'{expected[0]}x{expected[1]}'
        entry['px_ok'] = (pix.width >= expected[0] and pix.height >= expected[1]
                          and pix.width - expected[0] <= args.px_tolerance
                          and pix.height - expected[1] <= args.px_tolerance)
        if not entry['px_ok']:
            failures.append(f'figure {fig}: crop is {entry["px"]}, union rect at {args.dpi} dpi '
                            f'is {entry["px_expected"]} (stale crop or wrong --dpi)')

        hits = text_hits(pymupdf, page, items, panels[0]['page_idx'], rect,
                         args.overlap_tolerance)
        entry['text_overlap'] = hits
        if hits:
            failures.append(f'figure {fig}: crop overlaps {len(hits)} text block(s) on page '
                            f'{panels[0]["page_idx"]} — body text or a table body was swallowed')

        for path, text in texts.items():
            refs = len(re.findall(re.escape(name), text))
            leftovers = [Path(p['img_path']).name for p in panels
                         if re.search(re.escape(Path(p['img_path']).name), text)]
            entry[path] = {'refs': refs, 'leftover_panel_refs': leftovers}
            if refs != 1 or leftovers:
                failures.append(f'figure {fig} in {path}: {refs} references, '
                                f'leftover panel refs {leftovers}')
        report.append(entry)

    for path, text in texts.items():
        missing = [r for r in set(IMGREF.findall(text))
                   if not (mineru / args.images_subdir / r).exists()]
        if missing:
            failures.append(f'{path}: unresolved image references {missing}')

    if groups:
        out = args.contact_sheet or str(Path(tempfile.gettempdir()) / 'figure-contact-sheet.png')
        sheet(pymupdf, merged, out)
        report_out = out
    else:
        report_out = None
        print('no split multi-panel figures detected — if this PDF has multi-panel figures, '
              'check how their captions are punctuated (a colon after the number is normal '
              'in VLM output)', file=sys.stderr)

    print(json.dumps({'figures': report, 'contact_sheet': report_out, 'failures': failures},
                     ensure_ascii=False, indent=2))
    if report_out:
        print(f'\nRead {report_out} with vision_analyze: every panel present with its axes, '
              'titles and legends, no body or caption text.')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
