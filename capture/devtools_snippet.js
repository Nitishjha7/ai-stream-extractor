// Zero-install capture: paste into the DevTools Console on chatgpt.com /
// claude.ai / perplexity.ai / grok.com / gemini.google.com BEFORE sending your
// prompt. It wraps window.fetch, tees every streaming response from the chat
// endpoints, and keeps the full body. When the answer is done, run
//     __download()
// to save capture_<host>_<time>.json, then:
//     python -m extractor.cli capture_*.json
//
// Why tee and not just "Copy response" in the Network tab: Chrome often shows
// "Failed to load response data" for long SSE streams, and EventStream view
// only shows messages, not a copyable body. Tee-ing the ReadableStream gives
// us the exact bytes the page parsed, with zero effect on the UI.
(() => {
  const RULES = [
    /\/backend-(api|anon)\/(f\/)?conversations?(\/|\?|$)/,         // ChatGPT stream (+resume) + conversation JSON
    /\/api\/organizations\/.+\/completion/,                         // Claude.ai
    /\/rest\/sse\/perplexity_ask/,                                  // Perplexity
    /\/rest\/app-chat\/conversations\//,                            // Grok
    /StreamGenerate/,                                               // Gemini (XHR, see below)
  ];
  const match = (u) => RULES.some((r) => r.test(u));
  window.__captures = window.__captures || [];

  const origFetch = window.fetch;
  window.fetch = async function (input, init) {
    const url = input instanceof Request ? input.url : String(input);  // input may be a string, URL object or Request
    const res = await origFetch.apply(this, arguments);
    try {
      const ct = (res.headers.get("content-type") || "").toLowerCase();
      const streaming = ct.includes("event-stream") || ct.includes("ndjson");  // catch-all: any SSE/NDJSON, even if the URL changed
      if (res.body && res.ok && (streaming || match(url))) {
        const [a, b] = res.body.tee();
        const rec = { url, content_type: ct, started: Date.now(), body: "" };
        window.__captures.push(rec);
        (async () => {
          const reader = b.getReader(); const dec = new TextDecoder();
          for (;;) { const { done, value } = await reader.read(); if (done) break; rec.body += dec.decode(value, { stream: true }); }
          rec.ended = Date.now();
          console.log(`[capture] ${url} — ${rec.body.length} chars in ${rec.ended - rec.started} ms`);
        })();
        return new Response(a, { status: res.status, statusText: res.statusText, headers: res.headers });
      }
    } catch (e) { console.warn("[capture] skip", e); }
    return res;
  };

  // Gemini still uses XMLHttpRequest for StreamGenerate
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, url) {
    this.__url = url; return origOpen.apply(this, arguments);
  };
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    if (this.__url && match(String(this.__url))) {
      this.addEventListener("loadend", () => {
        window.__captures.push({ url: String(this.__url), body: this.responseText });
        console.log(`[capture] XHR ${this.__url} — ${this.responseText.length} chars`);
      });
    }
    return origSend.apply(this, arguments);
  };

  window.__download = () => {
    const blob = new Blob([JSON.stringify({ platform: location.host, captured_at: new Date().toISOString(), responses: window.__captures }, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `capture_${location.host}_${Date.now()}.json`;
    a.click();
  };
  console.log("[capture] armed. Send your prompt, wait for the full answer, then run __download()");
})();
