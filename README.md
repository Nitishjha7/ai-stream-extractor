# ai-stream-extractor

Extract **fan-out queries**, **retrieved vs cited sources**, **citation snapshots** (incl. YouTube metadata) and **reasoning** from the network streams of AI assistants — ChatGPT first, then Claude, Perplexity, Grok and Gemini.

```
capture (browser)  ──►  raw body / HAR / capture.json  ──►  platform adapter  ──►  common model  ──►  JSON · Markdown · CSV
 devtools snippet         (one file per prompt)              (framing + semantics)    (Extraction)
 or Playwright
```

## Quick start

```bash
pip install playwright && playwright install chromium   # only needed for automated capture

# 1a. Manual capture (no install): open chatgpt.com, DevTools → Console,
#     paste capture/devtools_snippet.js, send the prompt, then run  __download()
# 1b. Or automated:
python capture/capture.py --platform chatgpt --prompt "best project management tools for small agencies"

# 2. Extract
python -m extractor.cli captures/capture_chatgpt_*.json --out out/
#   -> out/<name>.json  (full extraction)
#   -> out/<name>.md    (tables: fan-out queries, retrieved vs cited, YouTube snapshot)
#   -> out/<name>.csv   (one row per source, for Sheets)

# Also accepts a DevTools HAR export or a raw copied response body:
python -m extractor.cli chatgpt.har
python -m extractor.cli body.txt --platform chatgpt

# Tests (synthetic fixtures that mimic each wire format)
python samples/make_samples.py && python -m unittest discover -s tests -t .
```

## Layout

| Path | What |
|---|---|
| `capture/devtools_snippet.js` | Wraps `fetch`/XHR in the page, **tees** each chat stream and keeps the full body. Works where DevTools shows "Failed to load response data" for long SSE. |
| `capture/capture.py` | Playwright, persistent logged-in profile, types the prompt, records the stream (Playwright `response.body()` + in-page tee as fallback). |
| `extractor/sse.py` | Forgiving SSE/NDJSON reader (CRLF, `[DONE]`, keep-alives, non-JSON frames). |
| `extractor/platforms/chatgpt.py` | Replays ChatGPT's **delta encoding v1** (`add`/`append`/`replace`/`truncate`/`patch`, bare `{"v":…}` tokens re-using last path) to rebuild every message, then reads `metadata`. Also parses the `GET /backend-api/conversation/<id>` JSON. |
| `extractor/platforms/others.py` | Claude (Anthropic event types), Perplexity (block snapshots), Grok (NDJSON), Gemini (`)]}'` + length-prefixed `wrb.fr` with JSON-in-string), generic fallback. |
| `extractor/model.py` | `Extraction`/`Source` model, URL normalisation (strips `utm_source=chatgpt.com` so retrieved ↔ cited join), schema-agnostic deep walk. |
| `samples/` | **Synthetic** fixtures for tests — not real captures. |

## How "retrieved vs cited" is decided (ChatGPT)

- **Retrieved** = every entry in `metadata.search_result_groups[].entries[]` (each has `ref_id` like `turn0search3`).
- **Cited** = anything in `metadata.content_references[]` (`grouped_webpages`, `video`, `sources_footnote`, `supporting_websites`) **or** a `ref_id` that appears in the answer's private-use citation markers `citeturn0search3`.
- URLs are normalised before joining, because cited links get `?utm_source=chatgpt.com` appended.

Two independent signals for "cited" means a key rename on one side doesn't silently zero the result; a deep-walk safety net catches sources in containers we don't know yet.
