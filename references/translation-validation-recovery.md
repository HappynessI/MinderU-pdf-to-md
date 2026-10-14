# Recovering from a failed final translation validation

`translate_markdown.py` raises 「最终 Markdown 中的公式、代码、链接、图片或 HTML 结构发生变化」 after every chunk has already been translated and cached. Nothing is written to disk, the API calls are all paid for, and re-running the same command re-sends nothing (chunks are cache hits) but reproduces the same failure. Diagnose locally first.

## Why caching makes this cheap

The failure is a comparison between two documents, not a bad model answer. `.translation-state.json` holds each chunk's final translated text keyed by `cache_key`, so the assembled document can be rebuilt in-process and the mismatching protected values attributed to a single chunk.

## Recipe

Write the probe under the scratch directory and run it with the system `python3` (the translation script has no third-party imports):

```python
import sys, json
from collections import Counter
from pathlib import Path
sys.path.insert(0, "/Users/yihanwang/.hermes/skills/productivity/mineru-pdf-to-md/scripts")
import translate_markdown as T, markdown_postprocess as MP

base = Path("<PAPER-mineru>")
source = base / "full.md"
state = json.loads((base / "translation-zh-CN" / ".translation-state.json").read_text(encoding="utf-8"))

markdown = source.read_text(encoding="utf-8")
markdown, _ = MP.normalize_algorithm_blocks(markdown)
tsource = T.replace_html_tables_with_images(
    markdown, source, table_mode="image", content_list_path=None).markdown
assert state["source_sha256"] == T.sha256_text(markdown)          # your pipeline matches the run
assert state["translation_source_sha256"] == T.sha256_text(tsource)

a = T.build_parser().parse_args([str(source)])                    # the run's own defaults
dnt, terms, dnt_hash = T.load_do_not_translate(a.do_not_translate_file)
glossary, gh = T.load_glossary(a.glossary_file)
model, base_url = T.normalize_model(a.model), T.normalize_base_url(a.base_url)

key = lambda text: T.cache_key(text, base_url=base_url, model=model,
    source_language=a.source_language, target_language=a.target_language,
    glossary_hash=gh, do_not_translate_hash=dnt_hash, thinking=a.thinking,
    processor_version="table-images-v1-do-not-translate-strong-markers-v1")

parts = T.document_parts(tsource, target_language=a.target_language,
                         translate_references=a.translate_references)
chunks = T.chunk_document(parts, a.max_chars)
for i, part in enumerate(chunks):
    if not part.translatable:
        continue
    entry = state["chunks"].get(key(part.text))
    if entry is None:
        print(i, "NO CACHE"); continue
    src = Counter(v for _p, v in T.protect_markdown(part.text, terms).replacements)
    tgt = Counter(v for _p, v in T.protect_markdown(entry["translation"], terms).replacements)
    if src != tgt:
        print(f"--- chunk {i} ---")
        print("  LOST", (src - tgt).most_common(10))
        print("  NEW ", (tgt - src).most_common(10))
```

Every translatable chunk printing `NO CACHE` means the keys do not line up — almost always because the probe called `load_do_not_translate(None)` instead of the parser's default `--do-not-translate-file` path, which yields empty terms and a different hash. Fix the probe: a key mismatch silently makes it compare the source against itself and report nothing.

## The two causes seen so far

| Symptom | Cause | Where the fix belongs |
|---|---|---|
| A short preserved URL (`https://arxiv.`, produced when the PDF line-broke a reference) matched inside the longer preserved URL (`https://arxiv.org/abs/2407.00402.`) and a space was inserted mid-URL, so the full URL vanished from the target multiset | A post-processing normalizer rewriting each preserved URL over the whole document | `normalize_preserved_url_boundaries` — scan maximal URL tokens in one pass and insert a space only when a token continues past a preserved URL into a word |
| One term (`token`) present far more often in the target than the source | The validation applied the strict multiset to do-not-translate terms, which a Chinese translation legitimately introduces (`tokens` → `token`) | `validate_final_document` — exclude terms from that comparison; `restore_markdown` already enforces them per chunk |
| Chunks that are mostly URLs (a datasheet or appendix Q&A, `(https://…/segment-anything)`) fail three structure retries, then every split level, and the run dies | `URL_TOKEN_PATTERN` stopped at ASCII `)` but not at the full-width `）` a translation produces, so the target token read `https://…/retinaface）来检测人脸` and the URL left the multiset | `URL_TOKEN_PATTERN` — also stop the token at CJK and full-width punctuation (`\u3000-\u303f`, `\u2018-\u201f`, `\uff00-\uffef`); keep CJK *ideographs* out of the exclusion set or `url获取` stops gaining its separating space |

A bibliography whose entries carry a line-break-split URL makes the first combination common, so treat the reference list as the first place to look when only URL values differ between the two documents.

## What correct output looks like

The bibliography is a non-translatable part appended verbatim, so it must be **byte-identical to the source**, broken URLs included (`https: //arxiv.org/abs/…`, `https://arxiv. org/abs/…`). Repairing those by hand puts characters into the translation that the original never printed, and it also changes the protected-value multiset the validator compares. Verify with `Counter(re.findall(r"https?://[^\s<>()。！？；，、]+", …))` on both files: the two counters must be equal, with `LOST` and `NEW` both empty.

## After fixing a script bug

Add a regression test to `tests/test_translate_markdown.py` that fails against the old behaviour — for the URL case, a source holding both `https://arxiv. org/abs/x.` and `https://arxiv.org/abs/y.`, asserting the identity transform leaves the text unchanged and that a glued Chinese suffix still gains its separating space. Run `python3 -m unittest discover -s tests` and push the fix in the same turn (see the sync section of SKILL.md).
