"""Automated capture with Playwright (one prompt -> one capture file per platform).

    pip install playwright && playwright install chromium
    python capture/capture.py --platform chatgpt --prompt "best project management tools for small agencies"

First run opens a visible browser with a persistent profile in ./.profile/<platform>;
log in by hand once, then press Enter in the terminal. Later runs reuse the session.

Design notes
* We listen with page.on("response") and await response.body(): Playwright
  resolves it only after the stream closes, so we get the complete SSE body.
* We also inject capture/devtools_snippet.js as a fallback (same tee logic),
  because on some sites Playwright returns an empty body for streamed fetches.
* Only your own account, one prompt at a time, human-like pacing. These are
  consumer UIs, not APIs: respect each site's terms and rate limits.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).parent
PLATFORMS = {
    "chatgpt":    {"url": "https://chatgpt.com/",            "resp": r"/backend-(api|anon)/(f/)?conversation$",
                   "input": "#prompt-textarea, div[contenteditable='true']"},
    "claude":     {"url": "https://claude.ai/new",           "resp": r"/api/organizations/.+/completion",
                   "input": "div[contenteditable='true']"},
    "perplexity": {"url": "https://www.perplexity.ai/",      "resp": r"/rest/sse/perplexity_ask",
                   "input": "textarea, div[contenteditable='true']"},
    "grok":       {"url": "https://grok.com/",               "resp": r"/rest/app-chat/conversations/",
                   "input": "textarea, div[contenteditable='true']"},
    "gemini":     {"url": "https://gemini.google.com/app",   "resp": r"StreamGenerate",
                   "input": "div[contenteditable='true']"},
}


async def capture(platform: str, prompt: str, out: Path, idle_s: float, headless: bool) -> Path:
    cfg = PLATFORMS[platform]
    rx = re.compile(cfg["resp"])
    responses, pending = [], set()

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(HERE.parent / ".profile" / platform), headless=headless,
            viewport={"width": 1280, "height": 900})
        await ctx.add_init_script(path=str(HERE / "devtools_snippet.js"))
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        async def on_response(resp):
            if not rx.search(resp.url) or resp.request.method != "POST":
                return
            pending.add(resp.url)
            try:
                body = (await resp.body()).decode("utf-8", "replace")
            except Exception as e:  # stream torn down, etc.
                print(f"  body() failed: {e}")
                body = ""
            responses.append({"url": resp.url, "status": resp.status, "body": body, "via": "playwright"})
            pending.discard(resp.url)
            print(f"  captured {resp.url} ({len(body)} chars)")

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await page.goto(cfg["url"], wait_until="domcontentloaded")

        box = page.locator(cfg["input"]).first
        try:
            await box.wait_for(timeout=15000)
        except Exception:
            input(f"[{platform}] Log in / clear any dialog in the browser, then press Enter here… ")
            await box.wait_for(timeout=60000)

        await box.click()
        await page.keyboard.type(prompt, delay=25)
        await page.keyboard.press("Enter")
        print(f"[{platform}] prompt sent, waiting for stream to finish…")

        # wait until we have at least one capture and nothing is pending for idle_s
        t0, last_change, n = time.time(), time.time(), 0
        while time.time() - t0 < 240:
            await asyncio.sleep(1)
            if len(responses) != n:
                n, last_change = len(responses), time.time()
            if responses and not pending and time.time() - last_change > idle_s:
                break

        # fallback / cross-check: bodies seen by the in-page tee
        tee = await page.evaluate("window.__captures || []")
        for r in tee:
            if r.get("body") and not any(x["body"] == r["body"] for x in responses):
                responses.append({"url": r["url"], "body": r["body"], "via": "page-tee"})
        await ctx.close()

    out.mkdir(parents=True, exist_ok=True)
    path = out / f"capture_{platform}_{int(time.time())}.json"
    path.write_text(json.dumps({"platform": platform, "prompt": prompt, "responses": responses}, ensure_ascii=False, indent=1))
    print(f"[{platform}] saved {len(responses)} response(s) -> {path}")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=[*PLATFORMS, "all"], required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", type=Path, default=Path("captures"))
    ap.add_argument("--idle", type=float, default=6.0, help="seconds of quiet before we stop")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    for p in (PLATFORMS if a.platform == "all" else [a.platform]):
        asyncio.run(capture(p, a.prompt, a.out, a.idle, a.headless))


if __name__ == "__main__":
    main()
