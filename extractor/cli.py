"""Command line entry point.

    python -m extractor.cli <capture> [--platform auto|chatgpt|claude|perplexity|grok|gemini]
                                      [--out out/]

<capture> can be:
  * a raw response body saved from DevTools (Network -> request -> Response -> copy)
  * a .har file exported from DevTools (all matching requests are processed)
  * a .json written by capture/capture.py ({"platform":..., "responses":[{url, body}]})

Outputs <out>/<name>.json (full extraction) and <name>.md (tables for the write-up).
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sys
from pathlib import Path

from .platforms import chatgpt
from .platforms.others import parse_claude, parse_gemini, parse_grok, parse_perplexity, parse_generic

URL_RULES = [  # (regex on request URL, platform)
    (r"chatgpt\.com/backend-(api|anon)/(f/)?conversations?(/|\?|$)", "chatgpt"),
    (r"claude\.ai/api/.+/completion", "claude"),
    (r"perplexity\.ai/rest/sse/perplexity_ask", "perplexity"),
    (r"grok\.com/rest/app-chat/conversations/.+", "grok"),
    (r"gemini\.google\.com/.+StreamGenerate", "gemini"),
]
PARSERS = {"chatgpt": chatgpt.parse, "claude": parse_claude, "perplexity": parse_perplexity,
           "grok": parse_grok, "gemini": parse_gemini}


def sniff(text: str) -> str:
    head = text[:20000]
    if "delta_encoding" in head or '"mapping"' in head or "search_model_queries" in text:
        return "chatgpt"
    if "content_block_start" in head or "message_start" in head:
        return "claude"
    if "web_results" in text or "perplexity" in head:
        return "perplexity"
    if head.lstrip().startswith(")]}'") or "wrb.fr" in head:
        return "gemini"
    if '"result"' in head and '"response"' in head:
        return "grok"
    return "unknown"


def load_bodies(path: Path) -> list[tuple[str | None, str]]:
    """Return list of (url, body) pairs."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".har":
        har = json.loads(raw)
        out = []
        for e in har["log"]["entries"]:
            url = e["request"]["url"]
            if not any(re.search(rx, url) for rx, _ in URL_RULES):
                continue
            c = e["response"].get("content", {})
            body = c.get("text") or ""
            if c.get("encoding") == "base64":
                body = base64.b64decode(body).decode("utf-8", "replace")
            if body:
                out.append((url, body))
        return out
    if path.suffix == ".json":
        try:
            j = json.loads(raw)
            if isinstance(j, dict) and "responses" in j:
                return [(r.get("url"), r.get("body", "")) for r in j["responses"]]
        except json.JSONDecodeError:
            pass
    return [(None, raw)]


def platform_for(url: str | None, body: str, forced: str) -> str:
    if forced != "auto":
        return forced
    if url:
        for rx, plat in URL_RULES:
            if re.search(rx, url):
                return plat
    return sniff(body)


def run(path: Path, forced: str = "auto"):
    results = []
    for url, body in load_bodies(path):
        plat = platform_for(url, body, forced)
        fn = PARSERS.get(plat)
        ex = fn(body) if fn else parse_generic(body, plat)
        if url:
            ex.notes.append(f"request: {url}")
        results.append(ex)
    return results


# ------------------------------------------------------------------ rendering
def _cell(x, n=90) -> str:
    s = "" if x is None else str(x)
    s = s.replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= n else s[: n - 1] + "…"


def to_markdown(d: dict) -> str:
    L = [f"# {d['platform']} extraction", ""]
    if d.get("prompt"):
        L += [f"**Prompt:** {d['prompt']}", ""]
    s = d["summary"]
    L += [f"**Fan-out queries:** {s['n_queries']} · **Sources:** {s['n_sources_total']} "
          f"(retrieved {s['n_retrieved']}, cited {s['n_cited']}, "
          f"retrieved-but-not-cited {s['n_retrieved_not_cited']}) · **Videos:** {s['n_video']}", ""]
    L += ["## Fan-out queries", ""] + [f"{i}. {q}" for i, q in enumerate(d["fanout_queries"], 1)] + [""]
    if d["reasoning"]:
        L += ["## Reasoning (as streamed)", ""] + [f"- {_cell(r, 300)}" for r in d["reasoning"]] + [""]
    L += ["## Sources — retrieved vs cited", "",
          "| # | Cited | Retrieved | Title | Domain | Date | URL | Snippet |",
          "|---|---|---|---|---|---|---|---|"]
    srcs = sorted(d["sources"], key=lambda s: (not s["cited"], not s["retrieved"]))
    for i, x in enumerate(srcs, 1):
        L.append(f"| {i} | {'✅' if x['cited'] else ''} | {'✅' if x['retrieved'] else ''} | {_cell(x['title'], 60)} "
                 f"| {_cell(x['domain'], 30)} | {_cell(x['pub_date'], 20)} | {_cell(x['url'], 70)} | {_cell(x['snippet'], 120)} |")
    vids = [x for x in d["sources"] if x["is_video"]]
    if vids:
        L += ["", "## YouTube / video snapshots", "", "| Title | Channel | Views | Likes | Verified | Description |", "|---|---|---|---|---|---|"]
        for v in vids:
            m = v["video"]
            L.append(f"| {_cell(v['title'], 50)} | {_cell(m.get('channel') or m.get('channel_name') or m.get('author'), 30)} "
                     f"| {_cell(m.get('views') or m.get('view_count'))} | {_cell(m.get('likes') or m.get('like_count'))} "
                     f"| {_cell(m.get('verified') or m.get('is_verified'))} | {_cell(m.get('description') or v['snippet'], 160)} |")
    if d["notes"]:
        L += ["", "## Notes", ""] + [f"- {n}" for n in d["notes"]]
    return "\n".join(L) + "\n"


def write_csv(d: dict, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["platform", "cited", "retrieved", "title", "domain", "pub_date", "url", "snippet", "is_video", "video_meta"])
        for s in d["sources"]:
            w.writerow([d["platform"], s["cited"], s["retrieved"], s["title"], s["domain"], s["pub_date"],
                        s["url"], s["snippet"], s["is_video"], json.dumps(s["video"], ensure_ascii=False)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Extract fan-out queries + citations from AI assistant network captures")
    ap.add_argument("capture", type=Path)
    ap.add_argument("--platform", default="auto", choices=["auto", *PARSERS, "generic"])
    ap.add_argument("--out", type=Path, default=Path("out"))
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    results = run(a.capture, a.platform)
    if not results:
        print("No matching responses found in capture.", file=sys.stderr)
        return 1
    for i, ex in enumerate(results):
        d = ex.to_dict()
        stem = f"{a.capture.stem}_{d['platform']}" + (f"_{i}" if len(results) > 1 else "")
        (a.out / f"{stem}.json").write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        (a.out / f"{stem}.md").write_text(to_markdown(d), encoding="utf-8")
        write_csv(d, a.out / f"{stem}.csv")
        s = d["summary"]
        print(f"[{d['platform']}] queries={s['n_queries']} sources={s['n_sources_total']} "
              f"retrieved={s['n_retrieved']} cited={s['n_cited']} -> {a.out / stem}.{{json,md,csv}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
