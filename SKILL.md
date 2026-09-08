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

MinerU may emit one visual figure as several adjacent `chart` blocks when the PDF contains a multi-panel figure. Before accepting the Markdown, inspect the first page and every page containing a `Figure N` caption against the original PDF. If adjacent image blocks share one page, form a contiguous horizontal or vertical group, and one group-level `Figure N` caption, treat them as one figure: reconstruct a single crop from the original PDF page (preferred) or stitch the extracted crops in page order, replace the fragment references with one image reference, and keep the caption once. Do not use panel labels or sub-captions as evidence that panels are independent figures.

A `Contents`/`目录` section is a structural object, not ordinary prose. Join wrapped lines across page boundaries, parse the numeric section prefix (`1`, `2.1`, `2.1.1`, etc.), preserve the printed page number, and emit a nested Markdown list with indentation matching the numeric depth. Verify that every entry from the PDF appears exactly once and that no entry is stranded as a continuation paragraph. Keep the raw extraction only in an HTML comment if auditability is needed.

During Markdown translation, protect strong-emphasis delimiters (`**` and `__`) as ordered placeholders while translating the phrase inside them. After restoring placeholders, compare the whitespace immediately before and after every delimiter with the source and restore missing spaces. In particular, `**Lead phrase** Following text` must remain a distinct bold phrase followed by a separate sentence, even when the target language normally omits spaces.

For each converted report, perform a targeted visual QA pass: compare representative pages containing the title/first figure, the complete contents section, at least one later multi-panel figure, and any algorithm block. A conversion is incomplete if a group-level figure is fragmented, a contents entry is missing/duplicated, or the Markdown still contains the original flat contents dump.

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
- allows formulas, inline code, and protected terms to follow target-language word order while requiring the exact same protected-value multiset; image, link, URL, HTML, and fenced-code order remains fixed;
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
5. Compare representative headings, formulas, figure captions, table captions, and paragraphs with the source.

### 4. Report the result

- For PDF conversion, report the Markdown path, MinerU mode, and any known extraction limitations.
- For translation, report the translated Markdown path, shared or copied assets, preserved table bodies, untranslated bibliography behavior, and any failed asset references.
- State that the translation workflow produces Markdown rather than a rendered PDF or DOCX.
