"""Common output model + a schema-agnostic "deep walk" used by every adapter.

Why a deep walk: every platform nests sources at a different path and the
paths change between releases. Instead of hard-coding paths, we walk the
whole JSON tree and recognise *shapes* (a dict with a `url` + `title` is a
source; a list under a key like `queries` is a fan-out). Platform adapters
then add the semantic layer (retrieved vs cited, reasoning, ordering).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# keys that commonly hold YouTube / video metadata in citation snapshots
VIDEO_KEYS = (
    "channel", "channel_name", "author", "author_name", "subscribers", "followers",
    "subscriber_count", "verified", "is_verified", "views", "view_count", "likes",
    "like_count", "duration", "description", "upload_date", "thumbnail_url",
)
DATE_KEYS = ("pub_date", "publish_date", "published", "date", "published_at", "last_updated")
SNIPPET_KEYS = ("snippet", "text", "description", "summary", "content")
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "ref_src"}


def norm_url(u: str) -> str:
    """Normalise a URL so retrieved/cited lists can be joined reliably.
    ChatGPT appends ?utm_source=chatgpt.com to cited links, which would
    otherwise make the same page look like two different sources."""
    try:
        p = urlparse(u.strip())
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
        host = p.netloc.lower().removeprefix("www.")
        path = p.path.rstrip("/") or "/"
        return urlunparse((p.scheme.lower() or "https", host, path, "", urlencode(q), ""))
    except Exception:
        return u


@dataclass
class Source:
    url: str
    title: str | None = None
    snippet: str | None = None
    domain: str | None = None
    pub_date: Any = None
    origin: str | None = None          # where in the stream we found it (key path)
    retrieved: bool = False
    cited: bool = False
    is_video: bool = False
    video: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


@dataclass
class Extraction:
    platform: str
    prompt: str | None = None
    fanout_queries: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    sources: dict[str, Source] = field(default_factory=dict)   # keyed by norm_url
    answer_text: str | None = None
    notes: list[str] = field(default_factory=list)

    # ---- helpers -------------------------------------------------------
    def add_query(self, q: Any) -> None:
        if isinstance(q, dict):
            q = q.get("q") or q.get("query") or q.get("text")
        if isinstance(q, str) and q.strip() and q.strip() not in self.fanout_queries:
            self.fanout_queries.append(q.strip())

    def add_source(self, d: dict, *, retrieved=False, cited=False, origin=None) -> Source | None:
        url = d.get("url") or d.get("link") or d.get("uri")
        if not isinstance(url, str) or not url.startswith("http"):
            return None
        key = norm_url(url)
        s = self.sources.get(key)
        if s is None:
            s = self.sources[key] = Source(url=url, origin=origin)
        s.retrieved |= retrieved
        s.cited |= cited
        s.title = s.title or _first(d, ("title", "name"))
        s.snippet = s.snippet or _first(d, SNIPPET_KEYS)
        s.pub_date = s.pub_date or _iso(_first(d, DATE_KEYS))
        s.domain = s.domain or d.get("attribution") or d.get("domain") or urlparse(url).netloc
        host = urlparse(url).netloc
        rid = d.get("ref_id") if isinstance(d.get("ref_id"), dict) else {}
        if (re.search(r"(^|\.)youtube\.com$", host) and re.search(r"/(watch|shorts|live)\b", url)) or "youtu.be" in host \
                or rid.get("ref_type") in ("youtube", "video") or d.get("type") == "video" or d.get("content_type") == "video":
            s.is_video = True
        for k in VIDEO_KEYS:
            if k in d and d[k] not in (None, ""):
                s.video.setdefault(k, d[k])
        known = {"url", "link", "uri", "title", "name", *SNIPPET_KEYS, *DATE_KEYS, *VIDEO_KEYS, "attribution", "domain"}
        for k, v in d.items():
            if k not in known and not isinstance(v, (dict, list)) and v not in (None, ""):
                s.extra.setdefault(k, v)
        return s

    def to_dict(self) -> dict:
        srcs = [asdict(s) for s in self.sources.values()]
        retrieved = [s for s in srcs if s["retrieved"]]
        cited = [s for s in srcs if s["cited"]]
        return {
            "platform": self.platform,
            "prompt": self.prompt,
            "fanout_queries": self.fanout_queries,
            "reasoning": self.reasoning,
            "summary": {
                "n_queries": len(self.fanout_queries),
                "n_sources_total": len(srcs),
                "n_retrieved": len(retrieved),
                "n_cited": len(cited),
                "n_retrieved_not_cited": len([s for s in srcs if s["retrieved"] and not s["cited"]]),
                "n_cited_not_in_retrieved": len([s for s in srcs if s["cited"] and not s["retrieved"]]),
                "n_video": len([s for s in srcs if s["is_video"]]),
            },
            "sources": srcs,
            "answer_text": self.answer_text,
            "notes": self.notes,
        }


def _iso(v: Any) -> Any:
    """ChatGPT sends pub_date as unix seconds (float); make it readable."""
    if isinstance(v, (int, float)) and v > 1e8:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(v / 1000 if v > 1e12 else v, tz=timezone.utc).date().isoformat()
    return v


def _first(d: dict, keys) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


def walk(node: Any, path: str = ""):
    """Yield (path, key, value) for every dict entry in the tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}/{k}"
            yield p, k, v
            yield from walk(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}/{i}")


def looks_like_source(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    url = d.get("url") or d.get("link") or d.get("uri")
    return isinstance(url, str) and url.startswith("http") and any(k in d for k in ("title", "name", "snippet"))
