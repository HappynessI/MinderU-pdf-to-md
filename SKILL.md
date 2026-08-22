---
name: mineru-pdf-to-md
description: Convert local PDFs to structured Markdown with MinerU, and translate structure-heavy Markdown into another language with an OpenAI-compatible API. Use for PDF-to-Markdown conversion, MinerU parsing/OCR, or producing a translated Markdown artifact while preserving formulas, tables, links, and images. Do not use for ordinary document reading when no converted or translated artifact is requested.
---

# MinerU PDF to Markdown and Translation

Use the bundled scripts instead of recreating API code. Resolve `scripts/` relative to this skill directory, not the user's current directory.

## PDF to Markdown

```bash
python3 scripts/mineru_pdf_to_md.py INPUT.pdf -o OUTPUT_DIR --mode auto --yes
```

### Routing

- Use `--mode agent` for the token-free Agent API when the PDF is at most 10 MB and 20 pages and lightweight pipeline output is sufficient.
- Use `--mode precise` for complex layouts, formulas, tables, OCR, files up to 200 MB or 200 pages, or when the user requests VLM quality. It requires a configured Token.
- Use `--mode auto` by default: the script selects precise mode when it can resolve a Token, otherwise Agent mode.
- Add `--ocr` for scanned PDFs or PDFs with a broken text layer. Keep table and formula recognition enabled unless the user asks otherwise.

### Output modes

- Precise mode uses a compact output by default: `full.md`, `*_content_list.json`, and every file under `images/`. Unreferenced images are intentionally retained.
- Add `--keep-debug-artifacts` only when the user requests complete MinerU output or when diagnosing layout, reading-order, table, formula, or model errors. It retains the returned origin PDF, V2 content list, model output, and layout metadata.
- Agent mode returns only `full.md` because its API does not provide a result bundle.

### Authorization and privacy

This workflow uploads the source PDF to `mineru.net`. When the user explicitly asks to convert a specific PDF with this skill or MinerU, treat that request as authorization to upload only the named file. Do not ask for a second confirmation; tell the user that MinerU cloud conversion is starting and pass `--yes` automatically.

Obtain confirmation only when the upload scope is ambiguous, the request covers a directory or unspecified batch of files, or the file appears likely to contain sensitive information. Never extend authorization beyond the files the user identified.

Never print, paste, log, commit, or embed the Token. The script resolves it in this order:

1. `MINERU_API_TOKEN`
2. `--token-file` or `MINERU_API_TOKEN_FILE`
3. macOS Keychain service `mineru-pdf-to-md`
4. `~/.config/mineru-pdf-to-md/token`

To configure a Token interactively, run:

```bash
python3 scripts/configure_token.py
```

### Completion checks

1. Read the final JSON printed by the client and verify `markdown_path` exists.
2. Confirm the Markdown is non-empty and that local image links resolve in precise mode.
3. For complex PDFs, render representative original pages and compare reading order, headings, formulas, tables, figures, and captions against the Markdown.
4. Report the Markdown path, API mode, and any known extraction limitations.

Read [references/mineru-api.md](references/mineru-api.md) only when changing the client, diagnosing protocol failures, or explaining API limits.

## Markdown translation

Translate MinerU's `full.md`, or another Markdown document, without using the content-list or layout JSON as model input:

```bash
python3 scripts/translate_markdown.py INPUT.md \
  -o TRANSLATION_DIR \
  --target-language zh-CN \
  --model deepseek-v4-flash \
  --yes
```

The default output for `full.md` is `translation-zh-CN/full-CN.md`. Referenced local images are copied into the translation directory so the translated Markdown remains self-contained. The script does not render a PDF.

The translator:

- chunks at Markdown block boundaries and resumes from `.translation-state.json`;
- disables DeepSeek thinking by default because bulk translation does not benefit from reasoning tokens;
- protects formulas, code, image references, link destinations, URLs, and HTML tags;
- validates placeholder order, heading levels, and HTML table structure;
- translates figure/table captions and table prose while preserving tags and values;
- leaves bibliography entries unchanged by default and translates the section heading;
- accepts `--glossary-file` for document-specific terminology.

Use `--translate-references` only when the user explicitly wants bibliography entries translated. Use `--force` only when they ask to discard cached translations or when model/language settings have changed and the cache is unsuitable.

### Translation API configuration

The default provider is DeepSeek's OpenAI-compatible endpoint with `deepseek-v4-flash`. Override `--base-url` and `--model` for another compatible provider. The API Key is resolved in this order:

Use `--thinking auto` when a non-DeepSeek compatible provider rejects the default `thinking: disabled` request field. Enable thinking only when the user explicitly accepts the additional latency and token usage.

1. `MARKDOWN_TRANSLATION_API_KEY`
2. `DEEPSEEK_API_KEY`
3. `--api-key-file` or `MARKDOWN_TRANSLATION_API_KEY_FILE`
4. macOS Keychain service `mineru-markdown-translate`
5. `~/.config/mineru-pdf-to-md/translation-token`

Configure it interactively with:

```bash
python3 scripts/configure_translation_token.py
```

Never print, log, embed, or commit the API Key. The translation workflow sends Markdown text to the configured provider, but does not upload the original PDF or local images. Before sending content, tell the user which provider receives it and obtain confirmation; pass `--yes` only after authorization.

### Translation completion checks

1. Read the final JSON and verify `markdown_path` exists under the requested translation directory.
2. Confirm `api_calls`, `cache_hits`, copied asset count, and token usage are plausible.
3. Verify every relative image reference resolves from the translated Markdown.
4. Compare representative headings, formulas, HTML tables, figure captions, and paragraphs with the source.
5. Report that the artifact is Markdown-only and mention any untranslated bibliography or failed asset references.
