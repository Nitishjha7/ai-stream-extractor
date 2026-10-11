"""ChatGPT adapter.

Two inputs are supported:

1. The live SSE body of  POST /backend-api/(f/)conversation
   ChatGPT uses "delta encoding v1": the first event announces
   `event: delta_encoding / data: "v1"`, then every `event: delta` carries a
   JSON-Patch-like op  {p: path, o: op, v: value, c: channel}.
   - `o:"add"`   with p:""  -> a new message object starts
   - `o:"append"` / `"replace"` / `"truncate"` on a JSON-pointer path
   - `o:"patch"` -> v is a list of ops applied in order
   - an event with only {v: "..."} re-uses the previous p/o (token stream)
   We replay those ops to rebuild every message (assistant text, tool
   messages, thoughts) with its full `metadata`.

2. The conversation JSON from  GET /backend-api/conversation/<id>
   (a `mapping` of node-id -> {message}). This is the same data, already
   assembled; great as a cross-check and for back-filling old chats.

Where the interesting data lives (as observed in public write-ups and
DevTools; verify against your own capture, keys drift):
   metadata.search_model_queries.queries   -> fan-out queries
   metadata.search_queries[].q              -> fan-out queries (newer)
   metadata.search_result_groups[].entries -> RETRIEVED sources (+snippet)
   metadata.content_references[]           -> CITED sources (grouped_webpages,
                                             video, sources_footnote, ...)
   metadata.citations[].metadata           -> CITED (older format)
   content.content_type == "thoughts"      -> reasoning summaries
   content.content_type == "code" from a tool author with search(...) text
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from ..model import Extraction, walk, looks_like_source, norm_url
from ..sse import parse_sse, Event

QUERY_KEYS = {"search_model_queries", "search_queries", "queries"}
CITED_CONTAINERS = {"content_references", "citations", "sources_footnote", "safe_urls_cited"}
RETRIEVED_CONTAINERS = {"search_result_groups", "search_results", "entries"}


# ------------------------------------------------------------------ delta replay
def _ptr_parts(p: str) -> list[str]:
    if not p:
        return []
    return [x.replace("~1", "/").replace("~0", "~") for x in p.lstrip("/").split("/")]


def _get_parent(root: Any, parts: list[str]):
    cur = root
    for k in parts[:-1]:
        if isinstance(cur, list):
            k = int(k)
            while len(cur) <= k:
                cur.append({})
            cur = cur[k]
        else:
            cur = cur.setdefault(k, {})
    return cur, parts[-1]


def _apply(root: Any, op: dict) -> Any:
    p, o, v = op.get("p", ""), op.get("o", "replace"), op.get("v")
    parts = _ptr_parts(p)
    if o == "patch":
        for sub in v or []:
            root = _apply(root, sub)
        return root
    if not parts:  # whole-document op
        if o == "append" and isinstance(root, str):
            return root + v
        return copy.deepcopy(v) if o in ("add", "replace") else root
    parent, last = _get_parent(root, parts)
    if isinstance(parent, list):
        idx = len(parent) if last == "-" else int(last)
        if o == "append":
            if idx < len(parent):
                parent[idx] = (parent[idx] or "") + v if isinstance(v, str) else parent[idx] + v
            else:
                parent.append(v)
        elif o == "truncate":
            # truncate the *value* at this index (string or list) to length v
            if idx < len(parent) and isinstance(parent[idx], (str, list)):
                parent[idx] = parent[idx][: int(v)]
        else:
            if idx < len(parent):
                parent[idx] = v
            else:
                parent.append(v)
        return root
    cur = parent.get(last)
    if o == "append":
        if isinstance(cur, str) and isinstance(v, str):
            parent[last] = cur + v
        elif isinstance(cur, list):
            cur.extend(v if isinstance(v, list) else [v])
        elif cur is None:
            parent[last] = copy.deepcopy(v)
        else:
            parent[last] = v
    elif o == "truncate":
        if isinstance(cur, (list, str)):
            parent[last] = cur[: int(v)]
    elif o == "remove":
        parent.pop(last, None)
    else:  # add / replace
        parent[last] = copy.deepcopy(v)
    return root


def replay_delta_stream(events: list[Event]) -> tuple[list[dict], list[dict]]:
    """Return (messages, raw_payloads). Messages are rebuilt full objects."""
    docs: dict[Any, Any] = {}      # channel -> current document
    finished: list[dict] = []
    raw: list[dict] = []
    last_p: dict[Any, str] = {}
    last_o: dict[Any, str] = {}
    cur_c: Any = 0

    for ev in events:
        d = ev.data
        if not isinstance(d, dict):
            continue
        raw.append(d)
        # non-delta envelopes (title_generation, server_ste_metadata, message_stream_complete, ...)
        if ev.event not in (None, "delta") and "o" not in d and "v" not in d:
            continue
        if "type" in d and "v" not in d and "message" not in d:
            continue
        # legacy (pre delta-encoding) frames: full message each time
        if "message" in d and "v" not in d:
            msg = d["message"]
            mid = msg.get("id")
            docs[mid] = {"message": msg, **{k: v for k, v in d.items() if k != "message"}}
            continue
        if "c" in d:
            cur_c = d["c"]
        p = d.get("p", last_p.get(cur_c, ""))
        o = d.get("o", last_o.get(cur_c, "append"))
        if o == "add" and p == "" and cur_c in docs:
            finished.append(docs[cur_c])  # channel re-used for a new message
        docs[cur_c] = _apply(docs.get(cur_c), {"p": p, "o": o, "v": d.get("v")})
        if o != "patch":
            last_p[cur_c], last_o[cur_c] = p, o
        else:
            subs = d.get("v") or []
            if subs:
                last_p[cur_c], last_o[cur_c] = subs[-1].get("p", ""), subs[-1].get("o", "append")

    finished.extend(docs.values())
    msgs = []
    for doc in finished:
        if isinstance(doc, dict):
            msgs.append(doc.get("message", doc))
    return msgs, raw


# ------------------------------------------------------------------ extraction
def _msg_text(m: dict) -> str:
    c = m.get("content") or {}
    parts = c.get("parts") or []
    txt = [p for p in parts if isinstance(p, str)]
    if c.get("text"):
        txt.append(c["text"])
    return "".join(txt)


def extract_from_messages(msgs: list[dict], raw: list[dict] | None = None) -> Extraction:
    ex = Extraction(platform="chatgpt")
    answer_chunks = []
    ref_index: dict = {}   # "turn0search3" -> Source
    cited_refs: set = set()  # ref keys the answer actually cites

    for m in msgs:
        role = (m.get("author") or {}).get("role")
        ctype = (m.get("content") or {}).get("content_type")
        meta = m.get("metadata") or {}

        if role == "user" and not ex.prompt:
            ex.prompt = _msg_text(m) or None

        # ---- reasoning ("thoughts" / "reasoning_recap")
        if ctype == "thoughts":
            for t in (m.get("content") or {}).get("thoughts", []):
                s = " — ".join(x for x in (t.get("summary"), t.get("content")) if x)
                if s:
                    ex.reasoning.append(s)
        elif ctype == "reasoning_recap":
            ex.reasoning.append((m.get("content") or {}).get("content", ""))

        # ---- fan-out queries: metadata or a tool 'code' message like search("...")
        smq = meta.get("search_model_queries")
        if isinstance(smq, dict):
            for q in smq.get("queries", []):
                ex.add_query(q)
        for q in meta.get("search_queries", []) or []:
            ex.add_query(q)
        if ctype == "code" and role == "assistant":
            body = _msg_text(m)
            for q in re.findall(r'search\(\s*["\'](.+?)["\']\s*\)', body):
                ex.add_query(q)
            try:
                j = json.loads(body)
                for q in j.get("search_query", []) or j.get("queries", []) or []:
                    ex.add_query(q)
            except Exception:
                pass

        # ---- retrieved: search_result_groups -> entries
        for g in meta.get("search_result_groups", []) or []:
            for e in g.get("entries", []) or []:
                s = ex.add_source(e, retrieved=True, origin="metadata.search_result_groups")
                if s and g.get("domain"):
                    s.domain = s.domain or g["domain"]
                rid = e.get("ref_id")
                if s and isinstance(rid, dict):
                    key = f"turn{rid.get('turn_index')}{rid.get('ref_type')}{rid.get('ref_index')}"
                    s.extra["ref_id"] = key
                    ref_index[key] = s

        # ---- cited: content_references (+ older 'citations')
        for ref in meta.get("content_references", []) or []:
            items = ref.get("items") or []
            if ref.get("type") == "sources_footnote":
                items = ref.get("sources") or []
            for it in items or [ref]:
                ex.add_source(it, cited=True, origin=f"content_references[{ref.get('type')}]")
                for sw in it.get("supporting_websites", []) or []:
                    ex.add_source(sw, cited=True, origin="content_references.supporting_websites")
        # 2026 format: answer markdown carries <Cite refs={["turn729185search4", ...]}/> and
        # metadata.genui_components[type=cite].entities[].ref lists the same refs
        for comp in meta.get("genui_components", []) or []:
            if comp.get("type") == "cite":
                for ent in comp.get("entities", []) or []:
                    if ent.get("ref"):
                        cited_refs.add(ent["ref"])
        for c in meta.get("citations", []) or []:
            ex.add_source(c.get("metadata") or c, cited=True, origin="metadata.citations")

        if role == "assistant" and ctype == "text" and (m.get("recipient") in (None, "all")):
            t = _msg_text(m)
            if t:
                answer_chunks.append(t)

    # ---- safety net: deep-walk everything (catches key drift / new containers)
    for blob in [*msgs, *(raw or [])]:
        for path, k, v in walk(blob):
            if k in QUERY_KEYS and isinstance(v, list) and v and all(isinstance(x, (str, dict)) for x in v):
                for q in v:
                    ex.add_query(q)
            elif k == "search_model_queries" and isinstance(v, dict):
                for q in v.get("queries", []):
                    ex.add_query(q)
            if isinstance(v, list):
                cited = any(c in path for c in CITED_CONTAINERS)
                retrieved = any(c in path for c in RETRIEVED_CONTAINERS)
                for item in v:
                    if looks_like_source(item):
                        ex.add_source(item, cited=cited, retrieved=retrieved or not cited, origin=path)

    # Cited links that were never in the retrieval set (e.g. footnote-only) still count as retrieved
    # for the purposes of "retrieved vs cited" only if the model had them; we leave them flagged so
    # the report can surface that asymmetry explicitly.
    ex.answer_text = "\n".join(answer_chunks) or None
    if ex.answer_text:
        # Inline citation markers in the raw answer look like  \ue200cite\ue202turn0search3\ue202turn0news1\ue201
        # (private-use unicode delimiters). Resolve them back to retrieved entries via ref_id.
        cited_refs.update(re.findall(r"turn\d+[a-z]+\d+", ex.answer_text))
        # Plain markdown links in the answer: mark matching sources cited; keep the rest as a finding
        by_norm = {norm_url(s.url): s for s in ex.sources.values()}
        unmatched = []
        for link in re.findall(r"\]\((https?://[^)\s]+)\)", ex.answer_text):
            s = by_norm.get(norm_url(link))
            if s:
                s.cited = True
            elif link not in unmatched:
                unmatched.append(link)
        if unmatched:
            ex.notes.append(f"{len(unmatched)} links in the answer were NOT in the retrieved set (e.g. generated YouTube search URLs): "
                            + ", ".join(unmatched[:6]))
    hit = 0
    for key in cited_refs:
        if key in ref_index:
            ref_index[key].cited = True
            hit += 1
    if cited_refs:
        ex.notes.append(f"{len(cited_refs)} distinct citation refs in answer, {hit} resolved to retrieved entries")
    return ex


def parse_conversation_json(doc: dict) -> Extraction:
    mapping = doc.get("mapping") or {}
    # walk the tree in order (parent -> children) so prompt/answer come out in sequence
    order, seen = [], set()
    roots = [k for k, n in mapping.items() if not n.get("parent")]
    stack = list(reversed(roots))
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        order.append(nid)
        stack.extend(reversed(mapping[nid].get("children", [])))
    msgs = [mapping[n]["message"] for n in order if mapping[n].get("message")]
    ex = extract_from_messages(msgs)
    ex.notes.append("source: GET /backend-api/conversation/<id> (assembled conversation JSON)")
    return ex


def parse(text: str) -> Extraction:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            doc = json.loads(stripped)
        except json.JSONDecodeError:
            doc = None
        if isinstance(doc, dict) and "mapping" in doc:
            return parse_conversation_json(doc)
        if isinstance(doc, dict) and isinstance(doc.get("messages"), list):
            # 2026 format of GET /backend-api/conversations/<id>: flat ordered `messages` list
            ex = extract_from_messages(doc["messages"], [doc])
            ex.notes.append(f"source: GET conversation JSON (flat 'messages' list, {len(doc['messages'])} messages)")
            return ex
        if doc is not None:
            # unknown JSON shape (endpoint renamed / restructured): collect anything that
            # looks like a message, then let the deep walk find queries + sources
            msgs = [v for _, k, v in walk(doc) if k == "message" and isinstance(v, dict)]
            ex = extract_from_messages(msgs, [doc])
            ex.notes.append(f"source: plain JSON (no 'mapping'), {len(msgs)} message objects found; deep-walk used")
            return ex
    events = parse_sse(text)
    msgs, raw = replay_delta_stream(events)
    ex = extract_from_messages(msgs, raw)
    enc = next((e.data for e in events if e.event == "delta_encoding"), None)
    ex.notes.append(f"source: SSE stream, {len(events)} events, delta_encoding={enc!r}, {len(msgs)} messages rebuilt")
    return ex
