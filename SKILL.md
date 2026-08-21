---
name: mineru-pdf-to-md
description: Convert local PDF files to Markdown with MinerU's official cloud APIs when the user asks for PDF-to-Markdown conversion, MinerU parsing, OCR, or structure-preserving extraction. Do not use for ordinary PDF reading when no converted artifact is requested.
---

# MinerU PDF to Markdown

Use the bundled client instead of recreating API code:

```bash
python3 scripts/mineru_pdf_to_md.py INPUT.pdf -o OUTPUT_DIR --mode auto --yes
```

Resolve `scripts/` relative to this skill directory, not the user's current directory.

## Routing

- Use `--mode agent` for the token-free Agent API when the PDF is at most 10 MB and 20 pages and lightweight pipeline output is sufficient.
- Use `--mode precise` for complex layouts, formulas, tables, OCR, files up to 200 MB or 200 pages, or when the user requests VLM quality. It requires a configured Token.
- Use `--mode auto` by default: the script selects precise mode when it can resolve a Token, otherwise Agent mode.
- Add `--ocr` for scanned PDFs or PDFs with a broken text layer. Keep table and formula recognition enabled unless the user asks otherwise.

## Authorization and privacy

This workflow uploads the source PDF to `mineru.net`. Before running it, tell the user that the file will leave the local machine. If the current request does not explicitly authorize cloud conversion, obtain confirmation. Pass `--yes` only after authorization.

Never print, paste, log, commit, or embed the Token. The script resolves it in this order:

1. `MINERU_API_TOKEN`
2. `--token-file` or `MINERU_API_TOKEN_FILE`
3. macOS Keychain service `mineru-pdf-to-md`
4. `~/.config/mineru-pdf-to-md/token`

To configure a Token interactively, run:

```bash
python3 scripts/configure_token.py
```

## Completion checks

1. Read the final JSON printed by the client and verify `markdown_path` exists.
2. Confirm the Markdown is non-empty and that local image links resolve in precise mode.
3. For complex PDFs, render representative original pages and compare reading order, headings, formulas, tables, figures, and captions against the Markdown.
4. Report the Markdown path, API mode, and any known extraction limitations.

Read [references/mineru-api.md](references/mineru-api.md) only when changing the client, diagnosing protocol failures, or explaining API limits.
