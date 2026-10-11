"""Adapters for non-ChatGPT assistants.

Each one is deliberately thin: platform-specific framing/decoding first
(SSE vs NDJSON vs Google's length-prefixed batchexecute), then the shared
deep walk with platform key hints. Endpoint names and keys below are what
to look for in DevTools; confirm them in your own capture — they change.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..model import Extraction, walk, looks_like_source
from ..sse import parse_sse, parse_ndjson


def _deep(ex: Extraction, blobs, *, query_keys, retrieved_keys, cited_keys, reasoning_keys=()):
    for blob in blobs:
        for path, k, v in walk(blob):
            if k in query_keys:
                if isinstance(v, str):
                    ex.add_query(v)
                elif isinstance(v, list):
                    for q in v:
                        ex.add_query(q)
            if k in reasoning_keys and isinstance(v, str) and v.strip():
                if not ex.reasoning or ex.reasoning[-1] != v:
                    ex.reasoning.append(v)
            if isinstance(v, list):
                cited = any(c in path for c in cited_keys)
                retrieved = any(r in path for r in retrieved_keys)
                for item in v:
                    if looks_like_source(item):
                        ex.add_source(item, cited=cited, retrieved=retrieved or not cited, origin=path)


# ---------------------------------------------------------------- Perplexity
def _pplx_state(events) -> dict:
    """Perplexity (2026) streams `blocks[]`; each block is either a full block
    ({intended_usage, <field>: {...}}) or a `diff_block` = {field, patches:[{op, path, value}]}
    (JSON-Patch). Replay both into one dict: field -> final value."""
    from .chatgpt import _apply
    state: dict = {}
    for e in events:
        x = e.data
        if not isinstance(x, dict):
            continue
        for b in x.get("blocks") or []:
            db = b.get("diff_block")
            if db:
                f = db.get("field")
                for p in db.get("patches") or []:
                    state[f] = _apply(state.get(f), {"p": p.get("path", ""), "o": p.get("op", "replace"), "v": p.get("value")})
            else:
                for k, v in b.items():
                    if k != "intended_usage":
                        state[k] = v
    return state


def parse_perplexity(text: str) -> Extraction:
    """POST /rest/sse/perplexity_ask (SSE, `event: message`).
    Observed 2026-10 (real capture):
      workflow_block.steps[].items[]  WORKFLOW_ITEM_QUERIES  -> fan-out queries
                                      WORKFLOW_ITEM_SOURCES  -> sources seen by the step
                                      WORKFLOW_ITEM_TEXT     -> answer text
      web_result_block.web_results[]  -> RETRIEVED (name, url, snippet, timestamp, trust)
      sources_mode_block.rows[]       -> status SELECTED + citation:n = CITED
                                         status REVIEWED              = read, not cited
      answer_tabs_block.reformulated_queries -> extra queries (if any)
    """
    events = parse_sse(text)
    st = _pplx_state(events)
    ex = Extraction(platform="perplexity")
    first = next((e.data for e in events if isinstance(e.data, dict) and e.data.get("query_str")), None)
    if first:
        ex.prompt = first.get("query_str")

    answer = []
    for step in (st.get("workflow_block") or {}).get("steps") or []:
        if step.get("title"):
            ex.reasoning.append(f"step: {step['title']} ({step.get('tool_name') or '-'})")
        for it in step.get("items") or []:
            pl = it.get("payload") or {}
            for q in (pl.get("queries_payload") or {}).get("queries") or []:
                ex.add_query(q)
            for src in (pl.get("sources_payload") or {}).get("sources") or []:
                ex.add_source({**src, "title": src.get("name")}, retrieved=True, origin="workflow.sources_payload")
            tp = pl.get("text_payload") or {}
            if tp.get("text"):
                answer.append(tp["text"])
    for q in (st.get("answer_tabs_block") or {}).get("reformulated_queries") or []:
        ex.add_query(q)

    def with_title(w):
        return {**w, "title": w.get("name"), "pub_date": w.get("timestamp")}

    for w in (st.get("web_result_block") or {}).get("web_results") or []:
        s = ex.add_source(with_title(w), retrieved=True, origin="web_result_block")
        if s and isinstance(w.get("trust"), dict):
            s.extra["trust"] = w["trust"].get("name")
    statuses = {}
    for r in (st.get("sources_mode_block") or {}).get("rows") or []:
        w = r.get("web_result") or {}
        cited = r.get("citation") is not None
        s = ex.add_source(with_title(w), retrieved=True, cited=cited, origin="sources_mode_block.rows")
        if s:
            s.extra["perplexity_status"] = r.get("status")
            if cited:
                s.extra["citation_n"] = r.get("citation")
        statuses[r.get("status")] = statuses.get(r.get("status"), 0) + 1

    # older (pre diff_block) format: plan_block queries + markdown_block answer with [n] markers
    if not ex.fanout_queries:
        for _, k, v in walk(st):
            if k in ("queries", "query"):
                for q in (v if isinstance(v, list) else [v]):
                    ex.add_query(q)
    if not answer:
        answer = [v for _, k, v in walk(st) if k == "answer" and isinstance(v, str)][-1:]
    ex.answer_text = "\n".join(answer) or None
    ordered = (st.get("web_result_block") or {}).get("web_results") or []
    if ex.answer_text and ordered and not any(s.cited for s in ex.sources.values()):
        for n in {int(x) for x in re.findall(r"\[(\d+)\]", ex.answer_text)}:
            if 1 <= n <= len(ordered):
                ex.add_source(with_title(ordered[n - 1]), cited=True, retrieved=True, origin="answer [n] marker")
    ex.notes.append(f"{len(events)} SSE events; blocks replayed: {sorted(st)}; row statuses: {statuses}")
    return ex


# ---------------------------------------------------------------- Claude.ai
def parse_claude(text: str) -> Extraction:
    """POST /api/organizations/<org>/chat_conversations/<id>/completion (SSE,
    Anthropic Messages event types). Search shows up as a `tool_use` block
    (name web_search) whose input arrives as `input_json_delta` fragments ->
    {"query": ...}; results come back as a `tool_result` block listing pages
    (retrieved); the answer's `citation_start_delta` events name the pages
    actually cited. Thinking arrives as `thinking_delta` (summarised)."""
    events = parse_sse(text)
    ex = Extraction(platform="claude")
    blocks: dict[int, dict] = {}
    partial: dict[int, str] = {}
    thinking: dict[int, str] = {}
    answer = []
    for e in events:
        d = e.data
        if not isinstance(d, dict):
            continue
        t = d.get("type")
        if t == "content_block_start":
            i, cb = d.get("index"), d.get("content_block") or {}
            blocks[i] = cb
            if cb.get("type") in ("tool_result", "web_search_tool_result"):
                for item in cb.get("content") or []:
                    if isinstance(item, dict):
                        ex.add_source({**item, **(item.get("metadata") or {})}, retrieved=True, origin="tool_result")
            for c in cb.get("citations") or []:
                ex.add_source(c, cited=True, origin="text.citations")
        elif t == "content_block_delta":
            i, dl = d.get("index"), d.get("delta") or {}
            dt = dl.get("type")
            if dt == "input_json_delta":
                partial[i] = partial.get(i, "") + dl.get("partial_json", "")
            elif dt == "thinking_delta":
                thinking[i] = thinking.get(i, "") + dl.get("thinking", "")
            elif dt == "thinking_summary_delta":  # claude.ai streams short summaries, not raw thinking
                summ = (dl.get("summary") or {}).get("summary")
                if summ:
                    ex.reasoning.append(summ)
            elif dt == "text_delta":
                answer.append(dl.get("text", ""))
            elif dt in ("citation_start_delta", "citations_delta"):
                c = dl.get("citation") or {}
                ex.add_source(c, cited=True, origin=dt)
        elif t == "content_block_stop":
            i = d.get("index")
            if i in partial:
                try:
                    inp = json.loads(partial[i])
                    ex.add_query(inp.get("query") or inp.get("q"))
                except Exception:
                    ex.notes.append(f"unparsed tool input: {partial[i][:120]}")
    ex.reasoning.extend(v for v in thinking.values() if v.strip())
    ex.answer_text = "".join(answer) or None
    # safety net
    _deep(ex, [e.data for e in events if isinstance(e.data, dict)],
          query_keys={"query"}, retrieved_keys={"content"}, cited_keys={"citation"})
    ex.notes.append(f"{len(events)} SSE events")
    return ex


# ---------------------------------------------------------------- Grok
def parse_grok(text: str) -> Extraction:
    """POST /rest/app-chat/conversations/new (or .../responses): NDJSON, one
    `{"result":{"response":{...}}}` per line. Tokens stream as `token`;
    search shows up as tool-usage cards / `webSearchResults` / `xSearchResults`
    objects; reasoning tokens are flagged `isThinking`."""
    events = parse_sse(text)  # falls back to NDJSON
    blobs = [e.data for e in events if isinstance(e.data, (dict, list))]
    ex = Extraction(platform="grok")
    answer, thoughts = [], []
    for b in blobs:
        r = (b.get("result") or {}).get("response") or b.get("result") or b if isinstance(b, dict) else {}
        if isinstance(r, dict) and isinstance(r.get("token"), str):
            (thoughts if r.get("isThinking") else answer).append(r["token"])
    _deep(ex, blobs,
          query_keys={"query", "queries", "searchQuery", "search_query"},
          retrieved_keys={"webSearchResults", "xSearchResults", "results"},
          cited_keys={"citedWebSearchResults", "citations", "cardAttachment"})
    if thoughts:
        ex.reasoning.append("".join(thoughts))
    ex.answer_text = "".join(answer) or None
    ex.notes.append(f"{len(events)} frames")
    return ex


# ---------------------------------------------------------------- Gemini
def decode_batchexecute(text: str) -> list[Any]:
    """Gemini web: POST .../BardFrontendService/StreamGenerate. Body starts
    with the XSSI guard `)]}'`, then repeated <length>\\n<json-array> chunks.
    Each chunk is like [["wrb.fr", null, "<JSON STRING>"]] — the payload is a
    JSON document *inside a string*, made of positional arrays (no keys)."""
    body = text.lstrip()
    if body.startswith(")]}'"):
        body = body[4:]
    out = []
    dec = json.JSONDecoder()
    i = 0
    while i < len(body):
        m = re.compile(r"\s*(\d+)\s*\n").match(body, i)
        if m:
            i = m.end()
        try:
            obj, j = dec.raw_decode(body, i)
        except json.JSONDecodeError:
            i += 1
            continue
        i = j
        out.append(obj)
    # unwrap inner JSON strings recursively
    def unwrap(x):
        if isinstance(x, str) and x[:1] in "[{" and len(x) > 2:
            try:
                return unwrap(json.loads(x))
            except Exception:
                return x
        if isinstance(x, list):
            return [unwrap(y) for y in x]
        if isinstance(x, dict):
            return {k: unwrap(v) for k, v in x.items()}
        return x
    return [unwrap(o) for o in out]


def parse_gemini(text: str) -> Extraction:
    chunks = decode_batchexecute(text)
    ex = Extraction(platform="gemini")
    ex.notes.append(f"{len(chunks)} batchexecute chunks; payload is positional arrays (no keys) — "
                    "sources found by URL heuristics, titles taken from the nearest sibling string")
    seen = set()

    def visit(node):
        if isinstance(node, list):
            strs = [x for x in node if isinstance(x, str)]
            for idx, s in enumerate(node):
                if isinstance(s, str) and s.startswith("http") and not re.search(r"(gstatic|googleusercontent|google\.com/(search|url))", s):
                    if s in seen:
                        continue
                    seen.add(s)
                    title = next((t for t in strs if t != s and not t.startswith("http") and 3 < len(t) < 200), None)
                    ex.add_source({"url": s, "title": title}, retrieved=True, origin="positional")
            for x in node:
                visit(x)
    for c in chunks:
        visit(c)
    # longest plain-text string is usually the answer draft
    longest = ""
    def longest_str(n):
        nonlocal longest
        if isinstance(n, str) and len(n) > len(longest) and not n.startswith("http"):
            longest = n
        elif isinstance(n, list):
            for y in n:
                longest_str(y)
    for c in chunks:
        longest_str(c)
    ex.answer_text = longest or None
    return ex


# ---------------------------------------------------------------- generic
def parse_generic(text: str, platform: str = "unknown") -> Extraction:
    events = parse_sse(text)
    blobs = [e.data for e in events if isinstance(e.data, (dict, list))]
    if not blobs:
        try:
            blobs = [json.loads(text)]
        except Exception:
            blobs = []
    ex = Extraction(platform=platform)
    _deep(ex, blobs,
          query_keys={"query", "queries", "search_query", "search_queries", "q"},
          retrieved_keys={"results", "search_results", "web_results", "sources", "entries"},
          cited_keys={"citation", "citations", "references", "content_references"})
    ex.notes.append(f"generic deep-walk over {len(blobs)} JSON frames")
    return ex
