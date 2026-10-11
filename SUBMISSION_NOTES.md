# Beyond Labs — Network-stream analysis of AI assistants
**Nitish Kumar Jha** · Software Engineer & AI Automation Developer (LLM Research)

> Links: Loom [FILL] · GitHub [FILL] · Output JSON/CSV [FILL]
> Time spent: [FILL] · Tools: Chrome DevTools, Python 3.13, Playwright, Claude (AI pair-programmer for scaffolding parsers — every parser was then checked against my own captures)

---

## 1. Approach (how I broke it down)

1. **Find the right request first, parse later.** On chatgpt.com I filtered Network → Fetch/XHR and searched (Ctrl+F) for `search_model_queries`. That pointed to the streaming `POST /backend-api/f/conversation` (SSE) — and the same data also comes back assembled from `GET /backend-api/conversation/<id>` when the chat is reopened. I used both: the stream to see *order/timing*, the conversation JSON as a cross-check.
2. **Problem: DevTools can't give me the body.** For long SSE responses Chrome shows "Failed to load response data", and the EventStream tab isn't copyable. So I wrote a small console snippet that wraps `window.fetch` and **tees** the ReadableStream — the page gets one copy, I keep the other — and downloads it as JSON. Same idea later automated with Playwright.
3. **Problem: the stream isn't plain JSON messages.** ChatGPT sends `event: delta_encoding / data: "v1"` and then JSON-Patch-style ops (`{p, o, v, c}`: `add`, `append`, `replace`, `truncate`, `patch` batches; and bare `{"v": "token"}` frames that silently reuse the previous path). Grepping a single frame misses data that's assembled across frames, so I **replay the ops** to rebuild every message with its full `metadata`, then extract.
4. **Define retrieved vs cited precisely** (details in §2) and normalise URLs, because cited links carry `?utm_source=chatgpt.com` and would otherwise look like different pages.
5. **Generalise:** one common output model, thin per-platform adapters for framing (SSE / NDJSON / Google batchexecute), plus a schema-agnostic deep walk as a safety net when keys drift.

## 2. Task 1 & 2 — ChatGPT, one query

**Prompt:** `[FILL — e.g. best project management tools for small agencies]`

**Where things live in the stream (rebuilt message `metadata`):**

| Data | Location | Notes |
|---|---|---|
| Fan-out queries | `metadata.search_model_queries.queries[]` (also `search_queries[].q` on newer builds) | on the assistant tool-call message |
| Retrieved sources | `metadata.search_result_groups[].entries[]` | url, title, snippet, pub_date (unix), attribution, `ref_id` = `turn0search3` |
| Cited sources | `metadata.content_references[]` (`grouped_webpages`, `video`, `sources_footnote`, `supporting_websites`) | + inline markers `citeturn0search3` in the answer text |
| Reasoning | messages with `content_type: "thoughts"` / `"reasoning_recap"` | summaries, not raw chain-of-thought |
| YouTube snapshot | video entries: title, channel, views/likes etc., **description with timecodes** in `snippet` | no transcript anywhere in the stream — matches your finding |

**Results** (paste from `out/<capture>.md`):

- Fan-out queries: [FILL — numbered list]
- Retrieved **[N]** · Cited **[N]** · Retrieved-but-not-cited **[N]**
- Citation snapshot table: [FILL — table / link to CSV]
- Observation: [FILL — e.g. which domains were read but never cited; whether the YouTube description timecodes were present; any cited URL that wasn't in the retrieved set]

## 3. Main task — other platforms

Fill the **Observed** column from my own captures; "Where to look" is what I checked first.

| Platform | Endpoint & framing | Fan-out queries? | Sources / citations? | Reasoning? | Observed |
|---|---|---|---|---|---|
| **ChatGPT** | `POST /backend-api/f/conversation` · SSE, delta-encoded JSON-Patch | ✅ explicit list in metadata | ✅ retrieved **and** cited separated, rich snippets | ✅ thought summaries | [FILL] |
| **Claude.ai** | `POST /api/organizations/<org>/chat_conversations/<id>/completion` · SSE, Anthropic event types (`content_block_start/delta/stop`) | `tool_use` block `web_search`, query arrives as `input_json_delta` fragments that must be concatenated | `tool_result` block = retrieved; `citation_start_delta` in text = cited | `thinking_delta` (summarised) | [FILL] |
| **Perplexity** | `POST /rest/sse/perplexity_ask` · SSE, each event a growing snapshot of `blocks[]` | plan / search steps carry the queries | `web_results[]` = retrieved (with snippet, date); cited = `[n]` markers in the answer indexing that list | plan "goals" text | [FILL] |
| **Grok** | `POST /rest/app-chat/conversations/...` · **NDJSON**, not SSE | tool-usage card with the search query | web / X search results objects; cited subset separately | tokens flagged as thinking | [FILL] |
| **Gemini** | `POST …/BardFrontendService/StreamGenerate` · `)]}'` guard + length-prefixed chunks, `["wrb.fr", null, "<JSON string>"]` | not seen as a labelled list [FILL] | URLs present but in **positional arrays with no keys** — only heuristics | [FILL] | [FILL] |

**How they differ from ChatGPT**
- ChatGPT is the only one that ships a *diff protocol*: you can't read the final state from any single frame. Perplexity sends full snapshots (last frame ≈ final state). Claude sends typed events where the tool input itself is fragmented JSON. Grok is line-delimited JSON. Gemini is the hardest: JSON inside strings inside arrays with no field names, so any extractor is index-based and fragile.
- "Retrieved ≠ cited" can be measured cleanly on ChatGPT, Claude and Perplexity (they expose both sets). On Gemini I could only get the shown sources [FILL / confirm].
- Blocked / didn't work: [FILL — e.g. Gemini query list not present; Cloudflare challenge in headless mode; account limits]

## 4. Automation — what I built

`capture/devtools_snippet.js` (zero-install capture) → `capture/capture.py` (Playwright, persistent logged-in profile, one prompt end-to-end) → `python -m extractor.cli <capture>` → JSON + Markdown tables + CSV. Auto-detects platform from the request URL or the body. Unit tests run on synthetic fixtures that mimic each wire format (clearly labelled — not real data).

## 5. Scaling it into a system

1. **Inputs:** a prompt set (by topic/brand), each run *N* times per platform — fan-out queries are non-deterministic, so single runs are anecdotes; frequency across runs is the signal.
2. **Orchestration:** a queue (Redis + workers, or n8n/Cloud Scheduler triggering Cloud Run jobs). Each worker owns one browser profile per platform/account, with rate limits and randomised pacing.
3. **Store raw first, parse later:** every raw body goes to object storage (GCS/S3) unchanged, keyed by platform/run. Parsers are versioned and re-runnable — when a platform changes its format I fix the parser and re-process history instead of losing data.
4. **Normalised tables (Postgres):** `prompts`, `runs`, `fanout_queries`, `sources` (url, domain, title), `run_sources` (retrieved, cited, rank, snippet, video meta). Answers questions like *cite rate per domain*, *which pages are read but never cited*, *query overlap across runs/platforms*, *does a YouTube description change citation rate*.
5. **Drift detection:** alert when a run that normally yields queries/sources returns zero, plus golden fixtures in CI. Formats here change without notice — this is the main operational risk.
6. **Alternatives:** a small browser extension for passive collection from real usage, and the conversation-JSON endpoint for back-filling.

## 6. What didn't work / with more time

- [FILL — honest list from your session, e.g. "DevTools response preview was empty for long streams → wrote the tee snippet"; "first parser grepped single frames and missed tokens appended via bare {v} frames"; "Gemini positional arrays…"]
- More time: run 10× per prompt to measure fan-out stability; compare logged-in vs logged-out ChatGPT; YouTube-specific prompt set to test the description-vs-transcript finding at scale; same prompts across all 5 platforms into one comparison dashboard.
