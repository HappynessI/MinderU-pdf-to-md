---
name: mineru-pdf-to-md
description: Convert local PDFs to structured Markdown with MinerU and translate English Markdown into Chinese with an OpenAI-compatible API; use when a PDF reference should first be converted to Markdown or when English Markdown needs Chinese translation while keeping result-table bodies unchanged; do not use when reading or translating DOCX, LaTeX, or other document formats the model can handle directly.
---

# MinerU PDF to Markdown and Translation

Use the bundled scripts instead of recreating API code. Resolve `scripts/` relative to this skill directory, not the user's current directory.

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

- Use `--mode agent` for the token-free Agent API when the PDF is at most 10 MB and 20 pages and lightweight pipeline output is sufficient.
- Use `--mode precise` for complex layouts, formulas, tables, OCR, files up to 200 MB or 200 pages, or when the user requests VLM quality.
- Use `--mode auto` by default: the script selects precise mode when it can resolve a Token, otherwise Agent mode.
- Add `--ocr` for scanned PDFs or PDFs with a broken text layer. Keep table and formula recognition enabled unless the user asks otherwise.

Run:

```bash
python3 scripts/mineru_pdf_to_md.py INPUT.pdf \
  -o OUTPUT_DIR \
  --mode auto \
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

Verify the conversion:

1. Read the final JSON and confirm that `markdown_path` exists.
2. Confirm that the Markdown is non-empty and local image links resolve in precise mode.
3. For complex PDFs, compare representative original pages with the Markdown for reading order, headings, formulas, tables, figures, and captions.

Read [references/mineru-api.md](references/mineru-api.md) only when changing the client, diagnosing protocol failures, or explaining API limits.

### 3. Translate English Markdown into Chinese

Translate MinerU's `full.md`, or another Markdown document, without using content-list or layout JSON as model input:

```bash
python3 scripts/translate_markdown.py INPUT.md \
  -o TRANSLATION_DIR \
  --target-language zh-CN \
  --model deepseek-v4-flash \
  --api-key-file ~/.codex/api/DeepSeek-API.md \
  --yes
```

The default result for `full.md` is:

```text
translation-zh-CN/
├── full-CN.md
├── .translation-state.json
└── images/
```

The translator:

- chunks at Markdown block boundaries and resumes from `.translation-state.json`;
- preserves HTML and Markdown table bodies exactly as written instead of sending their cells for translation;
- translates headings, body prose, figure captions, table captions, and explanatory text outside tables;
- protects formulas, code, image references, link destinations, URLs, and HTML tags;
- validates placeholder order and heading levels;
- leaves bibliography entries unchanged by default and translates the section heading;
- copies referenced local images into the translation directory.

Use `--glossary-file` for document-specific terminology. Use `--translate-references` only when the user explicitly wants bibliography entries translated. Use `--force` only when cached translations must be discarded or the model or language settings have changed.

Verify the translation:

1. Read the final JSON and confirm that `markdown_path` exists under the translation directory.
2. Confirm that API calls, cache hits, copied asset count, and token usage are plausible.
3. Verify that every relative image reference resolves from the translated Markdown.
4. Confirm that representative HTML and Markdown table bodies are unchanged while their captions and surrounding explanations are translated.
5. Compare representative headings, formulas, figure captions, table captions, and paragraphs with the source.

### 4. Report the result

- For PDF conversion, report the Markdown path, MinerU mode, and any known extraction limitations.
- For translation, report the translated Markdown path, copied assets, preserved table bodies, untranslated bibliography behavior, and any failed asset references.
- State that the translation workflow produces Markdown rather than a rendered PDF or DOCX.
