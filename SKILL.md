---
name: mineru-pdf-to-md
description: Convert local PDFs to structured Markdown with MinerU and translate English Markdown into Chinese with an OpenAI-compatible API; use when a PDF reference should first be converted to Markdown or when English Markdown needs Chinese translation while keeping result-table bodies unchanged; do not use when reading or translating DOCX, LaTeX, or other document formats the model can handle directly.
---

# MinerU PDF to Markdown and Translation

Use the bundled scripts instead of recreating API code. Resolve `scripts/` relative to this skill directory, not the user's current directory.

## Default table policy

For both PDF → Markdown and Markdown → Chinese, replace table bodies with original PDF table crops, not HTML-rendered screenshots or regenerated tables. Keep captions as text (translate them in Chinese output) and leave the table image contents unchanged. Validate each image path and correspondence to its table. If a crop is missing, obtain it from the original PDF when available; otherwise request the source or report incomplete conversion. Never silently fall back to HTML/Markdown tables. Already-converted files must also meet this policy before reuse. No table images are required for a document without tables.

## Algorithm policy

Render MinerU blocks explicitly marked with the `mineru-algorithm` class as fenced `text` pseudocode, not as HTML `div` prose. Decode HTML entities and simplify inline LaTeX commands into readable pseudocode symbols while preserving titles, line order, indentation, variables, and control flow. Apply the same normalization when translating an older Markdown file. Do not rewrite ordinary HTML blocks, prose equations, or normal code blocks.

### Figure and contents policy

MinerU may emit one visual figure as several adjacent `chart` blocks when the PDF contains a multi-panel figure. Before accepting the Markdown, inspect the first page and every page containing a `Figure N` caption against the original PDF. If adjacent image blocks share one page, form a contiguous horizontal or vertical group, and one group-level `Figure N` caption, treat them as one figure: reconstruct a single crop from the original PDF page (preferred) or stitch the extracted crops in page order, replace the fragment references with one image reference, and keep the caption once. Do not use panel labels or sub-captions as evidence that panels are independent figures. Use `scripts/merge_figure_panels.py` for this — see "Multi-panel figures: rebuild them from the PDF" below for the command, the detection rule, and the pitfalls; never re-derive the grouping by eye.

A `Contents`/`目录` section is a structural object, not ordinary prose. Join wrapped lines across page boundaries, parse the numeric section prefix (`1`, `2.1`, `2.1.1`, etc.), preserve the printed page number, and emit a nested Markdown list with indentation matching the numeric depth. Verify that every entry from the PDF appears exactly once and that no entry is stranded as a continuation paragraph. Keep the raw extraction only in an HTML comment if auditability is needed.

During Markdown translation, protect strong-emphasis delimiters (`**` and `__`) as ordered placeholders while translating the phrase inside them. After restoring placeholders, compare the whitespace immediately before and after every delimiter with the source and restore missing spaces. In particular, `**Lead phrase** Following text` must remain a distinct bold phrase followed by a separate sentence, even when the target language normally omits spaces.

For each converted report, perform a targeted visual QA pass: compare representative pages containing the title/first figure, the complete contents section, at least one later multi-panel figure, and any algorithm block. A conversion is incomplete if a group-level figure is fragmented, a contents entry is missing/duplicated, or the Markdown still contains the original flat contents dump.

## Multi-panel figures: rebuild them from the PDF (standard)

MinerU emits one multi-panel figure as several adjacent `chart` blocks. Rebuild each group into a single image cropped from the original PDF — this is the required output shape, not an optional polish. Run it right after PDF → Markdown conversion and **before** translation, so the translator only ever sees one reference per figure:

```bash
uv venv .pdfenv && uv pip install --python .pdfenv/bin/python pymupdf   # once; system python3 has no pymupdf
.pdfenv/bin/python scripts/merge_figure_panels.py PAPER.pdf \
  --mineru-dir OUTPUT_DIR --markdown full.md \
  --translation translation-zh-CN/full-CN.md
```

It writes `images/figure-NN.jpg`, replaces the group's refs with that one ref, keeps the group caption once, and keeps `(a)`/`(b)` panel labels as text lines after the image. Already-merged files are reported as skipped, so re-running is safe.

The padding is 5 pt, but an edge that would land inside a line of text is pulled inward to that line's near side (padding shrinks to 0, never grows), because MinerU's panel bbox often stops a few points above the paper's own `(a) … (b) …` sub-caption line and the unclamped box slices those glyphs in half. `crop_rect` holds that rule, and `verify_merged_figures.py` imports it, so the expected pixel size follows the clamp. A group the reader rejects leaves no crop behind — the image is deleted instead of sitting unreferenced in `images/`, where the contact sheet would show it as a figure that was never wired into the document.

Some detected groups are not one figure at all: MinerU can hang a `Figure N` caption on the `table` block that follows the figure, so the run looks like two panels while the paper prints a figure and its own `Table M` directly below it. The merge step refuses that run (`skip: unexpected content between panels`) and deletes the crop it had already written; `verify_merged_figures.py` reports it as `left unfolded` instead of failing, because the geometry checks belong to a merged crop. **Never hand-merge such a group** — crop the figure alone, give it its own reference under the closing caption's number, and leave the table's image untouched; the entry then carries two images with their own captions. A figure whose own labels sit outside MinerU's image bbox (a vertical `Image level` / `Region level` pair on the right edge) needs its crop widened to the page's text right edge: MinerU's box stops short and slices those glyphs. Confirm with the edge-ink probe before and after — ink in the outermost two pixel columns means a glyph is cut, and a clean crop has none on all four edges.

Two group shapes are reported and left untouched, and both take the manual-crop path: panels that do not all sit on one PDF page, and a run the reader rejects as `skip: unexpected content between panels` even when every panel shares one page. The second rejection comes from the reading order rather than from the geometry — a text block landing between panels, or a closing panel whose own caption list leads with the panel's internal label before the `Figure N:` entry, is enough to break run detection while the paper still prints one figure. Crop the union of those panels' bboxes from the page they share, reusing this script's own numbers so the result is indistinguishable from an automatic merge: content-list bbox read on the 0–1000 grid, scaled by `page.rect.width` and `page.rect.height`, 5 pt padding, 300 dpi, RGB — clamped off text lines exactly as `crop_rect` returns it, which is what the verifier re-derives; save it as `images/figure-NN.jpg` under the number the closing caption states; replace the panel references in `full.md` with that single reference plus the caption once. Re-run `verify_merged_figures.py` afterwards — it accepts a hand-built crop whose pixel size matches the bbox union, so the geometry, wiring and text-overlap checks still guard the manual path. Say in the report which figure needed the manual crop, and why.

Both `--markdown` and `--translation` are resolved **relative to `--mineru-dir`**, so pass the bare names (`full.md`, `translation-zh-CN/full-CN.md`). Prefixing them with the output directory writes the crops, prints a JSON key naming that path with the value `missing file`, and exits **0** without rewriting either Markdown file — the fragmented panel references survive behind a plausible-looking run. Read the JSON keys, not just the exit code: a key naming a Markdown path is a silent no-op, and the tell in the file is `grep -c '!\[' full.md` still counting one reference per panel.

How it decides, and why not to hand-roll the detection: panel membership comes from `<uuid>_content_list.json`. The closing block carries `Figure N: ...` — VLM output uses a colon as often as a period, and a period-only pattern silently matches nothing and reports "no split multi-panel figures found" — and every earlier panel has either an empty caption or only its own `(a)`/`(b)` label (which does **not** close the run). Captions live under the block type's own key: `chart_caption`, `image_caption`, `table_caption`. MinerU sometimes types one panel of a figure as a `table` block while keeping the figure caption on it; that block is a panel, whereas a table block carrying a `Table N` caption or no caption at all is never absorbed — so a real table image above a figure never ends up half table, half figure. Detecting groups in the Markdown instead ("consecutive image refs above a caption") silently swallows a table image that happens to sit above the figure, producing one image that is half table, half figure. Never widen or shrink a group by walking image refs alone.

The crop is the union of the panel bboxes (content-list coordinates are on a 0–1000 grid per page), scaled to page points, rendered at 300 dpi with 5 pt padding. MinerU's original per-panel crops stay in `images/` unreferenced.

Always verify the rebuilt figures before reporting the conversion complete, in two passes. Geometry, wiring and reference resolution first:

```bash
.pdfenv/bin/python scripts/verify_merged_figures.py PAPER.pdf --mineru-dir OUTPUT_DIR \
  --markdown full.md --translation translation-zh-CN/full-CN.md
```

It re-derives every group from the content list and checks that each merged image is referenced exactly once with no leftover panel references, that its pixel size matches the panel-bbox union at the conversion dpi (PyMuPDF rounds the clip outward, so up to three pixels more is rounding rather than a stale crop), that the crop rectangle overlaps no text-bearing block on its page, and that every image reference resolves. It exits non-zero on any failure and prints the contact sheet path.

Then the visual pass: read that contact sheet with `vision_analyze` — the image arrives in the conversation and the review proceeds on its description, so this works even when the model is text-first. A correct crop shows every panel with its axes, titles, and legends, and no body or caption text. Keep the sheet in the system temporary directory (the script's default) so the paper directory keeps its documented footprint. When the auxiliary vision channel is unavailable, the same review runs through the local `claude-us` CLI, which opens the file itself and describes it: `claude-us -p "读取图片 <绝对路径>（用 Read 工具打开）…逐张检查子面板是否完整、有没有半截文字…" --allowedTools "Read"`. Ask it to name the figure number in each verdict, so a real defect (`figure-18：最后一个子图注被底边切断`) is separable from an approximate reading, and copy the contact sheet to a stable scratch path first — the script overwrites the default sheet on every verify run.

Put verification code in a script file under the scratch directory rather than a shell heredoc: a heredoc carrying `&` — bitwise operators, backgrounding — is refused by the terminal guard before it runs.

## When to use

1. A PDF is provided as reference material: convert it to Markdown before reading, analyzing, or citing its contents.
2. An English Markdown document needs to be translated into Chinese while preserving formulas, links, images, document structure, and the original contents of result tables.

## When not to use

1. Reading DOCX, LaTeX, plain text, or other document formats the model can handle directly.
2. Translating DOCX, LaTeX, or other directly supported document formats; use their format-specific workflow instead.

## Workflow

### 1. Identify the task

- For a PDF input, continue with PDF conversion.
- For an English Markdown input, continue with Markdown translation.
- For DOCX, LaTeX, or another directly supported format, stop using this skill and route to the appropriate document workflow.

### 2. Convert PDF to Markdown

Choose the MinerU mode:

- Use `--mode precise` by default so the result includes original table crops. Agent mode is only permitted when the user explicitly opts out of image tables.
- Use `--mode precise` for complex layouts, formulas, tables, OCR, files up to 200 MB or 200 pages, or when the user requests VLM quality.
- Use `--mode precise --table-mode image` by default: the script selects precise mode when it can resolve a Token, otherwise Agent mode.
- Add `--ocr` for scanned PDFs or PDFs with a broken text layer. Keep table and formula recognition enabled unless the user asks otherwise.

Run:

```bash
python3 scripts/mineru_pdf_to_md.py INPUT.pdf \
  -o OUTPUT_DIR \
  --mode precise --table-mode image \
  --token-file ~/.codex/api/MinderU-API.md \
  --yes
```

Keep the default compact output:

```text
OUTPUT_DIR/
├── full.md
├── *_content_list.json
└── images/
```

- Precise mode retains every extracted image, including images not referenced by `full.md`.
- Add `--keep-debug-artifacts` only when the user requests complete MinerU output or when diagnosing layout, reading-order, table, formula, or model errors.
- Agent mode returns only `full.md` because its API does not provide a result bundle.
- PDF conversion automatically normalizes MinerU algorithm `div` blocks into fenced pseudocode; no additional model call is required.

Verify the conversion:

1. Read the final JSON and confirm that `markdown_path` exists.
2. Confirm that the Markdown is non-empty and local image links resolve in precise mode.
3. For complex PDFs, compare representative original pages with the Markdown for reading order, headings, formulas, tables, figures, and captions.
4. If the document contains algorithms, confirm that `algorithm_blocks` is plausible and no `mineru-algorithm` `div`, encoded comparison operator, or fragmentary inline LaTeX remains inside the normalized pseudocode.

Read [references/mineru-api.md](references/mineru-api.md) only when changing the client, diagnosing protocol failures, or explaining API limits.

Then rebuild split multi-panel figures (see "Multi-panel figures" above) before translating:

```bash
.pdfenv/bin/python scripts/merge_figure_panels.py INPUT.pdf --mineru-dir OUTPUT_DIR \
  --markdown full.md
```

If the translation already exists, pass `--translation translation-zh-CN/full-CN.md` to the same call so both Markdown files share the one merged image; the translator's state file is unaffected because only image references change.

### 3. Translate English Markdown into Chinese

Translate MinerU's `full.md`, or another Markdown document. The flat content list may be read locally to resolve MinerU table screenshots, but it is never sent to the translation model:

```bash
python3 scripts/translate_markdown.py INPUT.md \
  -o TRANSLATION_DIR \
  --target-language zh-CN \
  --model deepseek-v4-flash --table-mode image \
  --api-key-file ~/.codex/api/DeepSeek-API.md \
  --yes
```

The default result for `full.md` shares the source image directory instead of
duplicating it:

```text
paper-mineru/
├── full.md
├── images/
└── translation-zh-CN/
    ├── full-CN.md
    └── .translation-state.json
```

The translator:

- chunks at Markdown block boundaries, resumes from `.translation-state.json`, and recursively splits only a persistently failing chunk while caching both child and combined results;
- replaces every table body with the matching original MinerU table crop;
- fails when any table lacks a matching crop; do not silently keep HTML or Markdown table bodies;
- translates headings, body prose, figure captions, table captions, and explanatory text outside tables;
- protects formulas, code, image references, link destinations, URLs, and HTML tags;
- allows formulas, inline code, and protected terms to follow target-language word order while requiring the exact same protected-value multiset of formulas, code, images, links, URLs and HTML, and a fixed order for image, link, URL, HTML and fenced-code placeholders;
- compares do-not-translate terms by their per-chunk placeholders rather than by document-wide count, because a translation legitimately writes an English term where the source spelled it differently (`tokens` → `token`); the placeholder check in `restore_markdown` still catches a term the model dropped;
- leaves bibliography entries unchanged by default and translates the section heading;
- rewrites local image references so the source and translation share one image directory;
- copies referenced images only when `--copy-assets` is explicitly requested for a self-contained translation directory.
- appends `do-not-translate.md` to the translation prompt and protects every listed term so it remains exactly unchanged.

Default to `--table-mode image` in both conversion and translation. The converter replaces table bodies in `full.md` before reporting success; translation retains these images or replaces remaining tables using matching crops. Pass `--content-list PATH` to the translator when the content list is not beside the input. `html` or fallback-capable `auto` modes are allowed only when the user explicitly requests text tables.

Maintain permanent untranslated terms as Markdown bullets in `do-not-translate.md`, one term per item. Use `--do-not-translate-file` to select another list. Use `--glossary-file` for document-specific source-to-target terminology. Use `--translate-references` only when the user explicitly wants bibliography entries translated. Use `--force` only when cached translations must be discarded or the model or language settings have changed.

Verify the translation:

1. Read the final JSON and confirm that `markdown_path` exists under the translation directory.
2. Confirm that API calls, cache hits, shared or copied asset count, and token usage are plausible.
3. Verify that every relative image reference resolves from the translated Markdown.
4. Confirm that table images match the original tables and captions remain translated; confirm no HTML or Markdown table bodies remain in default image mode.
5. Unify terminology across the chunks before reporting; chunked translation renders one source term several ways (`Setting-2` beside `设置 2`, `逃避` beside `规避`). Count each candidate first (`grep -o 逃避 full-CN.md | wc -l`), pick the dominant rendering, replace globally with a short script, then re-read every sentence the replacement touched — a global replace applied to a verb breaks grammar. Unify the paper's own structural labels in the same pass (`Setting-<N>` → `设置 N`), and normalize figure/table caption numbering at line start to `图N：` / `表N：` so captions match the citation style used in the knowledge base — do it with `scripts/normalize_captions.py translation-zh-CN/full-CN.md`, never by hand and never with a bare `^图\s*\d+` substitution. The caption/in-text discriminator is positional: a number followed by an explicit separator (`.．。:：`) opens a caption wherever the line sits, while the space-separated form (`表 3 展示了…`) is a caption only when the previous non-empty line is an image reference or an `(a)`/`(b)` panel label. Replacing every line-start 图N/表N turns in-text references into captions; the script is idempotent, so re-run it after any later editing pass. Rewrite the translation only: `full.md` stays the source of record.
6. Compare representative headings, formulas, figure captions, table captions, and paragraphs with the source.
7. If the run dies with 「最终 Markdown 中的公式、代码、链接、图片或 HTML 结构发生变化」, rebuild the assembly locally from `.translation-state.json` before spending another API pass: every chunk is cached, so the difference localizes to one chunk in one command, and the cause is often a post-processing normalizer rather than the model's output — recipe in [references/translation-validation-recovery.md](references/translation-validation-recovery.md).
8. A run that dies with a read timeout or a dropped connection has not lost its work: `chat_completion` retries the request, a failure that survives those retries counts as one of the structure attempts, and an exhausted chunk is split into smaller pieces instead of ending the run. Re-run the same command — every finished chunk is a cache hit and the run resumes at the chunk that failed. **Raise `--request-timeout` (default 180 s) for a long document before re-running**: a 78-page report's single chunk (up to `--max-chars` of dense appendix text) can exceed the default and time out twice in a row on a healthy API, and 600–900 s is cheap. Lowering `--max-chars` instead is the expensive mistake — chunk size is part of every cache key, so the whole document re-translates.

### 4. Report the result

- For PDF conversion, report the Markdown path, MinerU mode, and any known extraction limitations.
- If split multi-panel figures were rebuilt, report which figures, that each was checked visually, and any group left unmerged.
- For translation, report the translated Markdown path, shared or copied assets, preserved table bodies, untranslated bibliography behavior, and any failed asset references.
- State that the translation workflow produces Markdown rather than a rendered PDF or DOCX.

## Keeping the published repo in sync (standing request)

This skill is published at https://github.com/HappynessI/MinderU-pdf-to-md (public, branch `main`); that repo holds exactly this directory. Whenever anything here changes — `SKILL.md`, `scripts/`, `tests/`, `references/`, `do-not-translate.md` — push the change in the same turn instead of leaving local edits unpublished:

```bash
cd "$(mktemp -d)" && git clone https://github.com/HappynessI/MinderU-pdf-to-md.git repo
rsync -a --delete --exclude .git ~/.hermes/skills/productivity/mineru-pdf-to-md/ repo/
cd repo && git add -A && git status --short        # review before committing
```

Run the suite first (`python3 -m unittest discover -s tests`) — CI runs the same command on Python 3.9 and 3.12, so keep new code and tests 3.9-safe. Commit in the repo's conventional style (`feat:` / `fix:` / `docs:`), one logical change per commit, then `git push`. Never commit credentials, `api/` token files, converted paper output, or `*-mineru/` directories. Verify the push landed (`gh repo view HappynessI/MinderU-pdf-to-md --json pushedAt`) rather than trusting the exit code alone. A `LibreSSL SSL_connect: SSL_ERROR_SYSCALL` failure on the push is transient on this host: retry the same command two or three times before suspecting the proxy configuration, then confirm the commit on the remote (`api.github.com/repos/<owner>/<repo>/commits?per_page=3`).
