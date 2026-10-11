"""Tiny, forgiving SSE / NDJSON reader.

Real captures are messy: CRLF line endings, `event:` lines without data,
`data: [DONE]`, non-JSON keep-alives, and sometimes the body is NDJSON
(one JSON object per line, no `data:` prefix). This module turns any of
those into a flat list of `Event(event, data)` where `data` is parsed JSON
when possible and the raw string otherwise.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class Event:
    event: str | None
    data: Any  # parsed JSON, or raw str if it wasn't JSON


def _try_json(s: str) -> Any:
    s = s.strip()
    if not s:
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return s


def parse_sse(text: str) -> list[Event]:
    """Parse an SSE body. Falls back to NDJSON if no `data:` lines exist."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "data:" not in text:
        return parse_ndjson(text)

    events: list[Event] = []
    ev_name: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal ev_name, data_lines
        if data_lines:
            raw = "\n".join(data_lines)
            if raw.strip() != "[DONE]":
                events.append(Event(ev_name, _try_json(raw)))
        ev_name, data_lines = None, []

    for line in text.split("\n"):
        if line == "":
            flush()
        elif line.startswith(":"):
            continue  # SSE comment / keep-alive
        elif line.startswith("event:"):
            ev_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" ") if line[5:6] == " " else line[5:])
        # id:/retry: are irrelevant for extraction
    flush()
    return events


def parse_ndjson(text: str) -> list[Event]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            out.append(Event(None, _try_json(line)))
    return out


def iter_json_objects(events: Iterable[Event]):
    for e in events:
        if isinstance(e.data, (dict, list)):
            yield e.data
