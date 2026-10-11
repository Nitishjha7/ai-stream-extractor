"""Generate SYNTHETIC fixture streams that mimic each platform's wire format.

These are NOT real captures. They exist so the parsers have unit tests and
so the pipeline can be demoed end-to-end. Replace them with your own
captures (captures/*.json) for the actual submission.
"""
import json
from pathlib import Path

OUT = Path(__file__).parent
PROMPT = "best project management tools for small agencies"


def sse(events):
    out = []
    for name, data in events:
        if name:
            out.append(f"event: {name}")
        out.append("data: " + (data if isinstance(data, str) else json.dumps(data)))
        out.append("")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------ ChatGPT (delta encoding v1)
yt_entry = {
    "type": "search_result", "url": "https://www.youtube.com/watch?v=AbC123xyz",
    "title": "Top 5 Project Management Tools for Agencies (2026)",
    "snippet": "00:00 Intro\n01:12 ClickUp\n03:40 Teamwork.com\n06:05 Asana ...",
    "ref_id": {"turn_index": 0, "ref_type": "video", "ref_index": 0},
    "pub_date": 1754000000.0, "attribution": "youtube.com", "content_type": "video",
    "channel_name": "Agency Ops Lab", "subscribers": "48.2K", "is_verified": False,
    "views": 15234, "likes": 611, "duration": "PT9M31S",
}
groups = [
    {"type": "search_result_group", "domain": "zapier.com", "entries": [
        {"type": "search_result", "url": "https://zapier.com/blog/best-project-management-software/",
         "title": "The best project management software in 2026", "snippet": "We tested 70+ apps ...",
         "ref_id": {"turn_index": 0, "ref_type": "search", "ref_index": 0}, "pub_date": 1756000000.0, "attribution": "zapier.com"}]},
    {"type": "search_result_group", "domain": "teamwork.com", "entries": [
        {"type": "search_result", "url": "https://www.teamwork.com/blog/agency-project-management-software/",
         "title": "Agency project management software", "snippet": "Built for client work ...",
         "ref_id": {"turn_index": 0, "ref_type": "search", "ref_index": 1}, "pub_date": None, "attribution": "teamwork.com"}]},
    {"type": "search_result_group", "domain": "reddit.com", "entries": [
        {"type": "search_result", "url": "https://www.reddit.com/r/agency/comments/xyz/pm_tool/",
         "title": "What PM tool does your agency use?", "snippet": "We moved from Asana to ...",
         "ref_id": {"turn_index": 0, "ref_type": "search", "ref_index": 2}, "attribution": "reddit.com"}]},
    {"type": "search_result_group", "domain": "youtube.com", "entries": [yt_entry]},
]
answer_tokens = ["For a small agency, ", "**ClickUp** and **Teamwork.com** are the usual shortlist ",
                 "citeturn0search0turn0search1. ",
                 "A good video walkthrough compares them side by side ",
                 "citeturn0video0."]
chatgpt = [("delta_encoding", '"v1"')]
chatgpt.append(("delta", {"p": "", "o": "add", "c": 0, "v": {"message": {
    "id": "u1", "author": {"role": "user"}, "content": {"content_type": "text", "parts": [PROMPT]}, "metadata": {}}}}))
chatgpt.append(("delta", {"p": "", "o": "add", "c": 1, "v": {"message": {
    "id": "t1", "author": {"role": "assistant"}, "content": {"content_type": "thoughts", "thoughts": []},
    "metadata": {}}}}))
chatgpt.append(("delta", {"p": "/message/content/thoughts", "o": "append", "c": 1, "v": [
    {"summary": "Clarifying the agency use case", "content": "User wants tools for small agencies; need client portals, time tracking."}]}))
chatgpt.append(("delta", {"p": "", "o": "add", "c": 2, "v": {"message": {
    "id": "q1", "author": {"role": "assistant"}, "recipient": "web.run",
    "content": {"content_type": "code", "text": ""}, "metadata": {}}}}))
chatgpt.append(("delta", {"p": "/message/metadata/search_model_queries", "o": "add", "c": 2, "v": {
    "type": "search_model_queries", "queries": [
        "best project management tools for small agencies 2026",
        "agency project management software client portal time tracking",
        "ClickUp vs Teamwork.com for agencies", "project management tool comparison youtube"]}}))
chatgpt.append(("delta", {"p": "", "o": "add", "c": 3, "v": {"message": {
    "id": "a1", "author": {"role": "assistant"}, "recipient": "all",
    "content": {"content_type": "text", "parts": [""]}, "metadata": {"search_result_groups": []}}}}))
chatgpt.append(("delta", {"o": "patch", "c": 3, "v": [
    {"p": "/message/metadata/search_result_groups", "o": "replace", "v": groups}]}))
chatgpt.append(("delta", {"p": "/message/content/parts/0", "o": "append", "c": 3, "v": answer_tokens[0]}))
for t in answer_tokens[1:]:
    chatgpt.append(("delta", {"v": t}))  # bare token: re-uses last path/op
chatgpt.append(("delta", {"p": "/message/metadata/content_references", "o": "add", "c": 3, "v": [
    {"matched_text": "citeturn0search0turn0search1", "type": "grouped_webpages",
     "items": [{"title": "The best project management software in 2026",
                "url": "https://zapier.com/blog/best-project-management-software/?utm_source=chatgpt.com",
                "attribution": "Zapier", "pub_date": 1756000000.0,
                "supporting_websites": [{"title": "Agency project management software",
                                         "url": "https://www.teamwork.com/blog/agency-project-management-software/?utm_source=chatgpt.com"}]}]},
    {"matched_text": "citeturn0video0", "type": "video",
     "url": "https://www.youtube.com/watch?v=AbC123xyz&utm_source=chatgpt.com",
     "title": yt_entry["title"], "channel_name": "Agency Ops Lab", "thumbnail_url": "https://i.ytimg.com/vi/AbC123xyz/hq.jpg"},
]}))
chatgpt.append((None, {"type": "message_stream_complete", "conversation_id": "synthetic"}))
(OUT / "chatgpt_synthetic.sse.txt").write_text(sse(chatgpt) + "data: [DONE]\n\n")

# ------------------------------------------------------------ Claude.ai (Anthropic SSE events)
claude = [("message_start", {"type": "message_start", "message": {"id": "m1", "role": "assistant"}}),
          ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Should search for current agency PM tools."}}),
          ("content_block_stop", {"type": "content_block_stop", "index": 0}),
          ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "name": "web_search", "input": {}}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"query\": \"agency project "}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "management tools 2026\"}"}}),
          ("content_block_stop", {"type": "content_block_stop", "index": 1}),
          ("content_block_start", {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_result", "name": "web_search", "content": [
              {"type": "knowledge", "title": "Best PM software", "url": "https://zapier.com/blog/best-project-management-software/", "metadata": {"site_domain": "zapier.com"}},
              {"type": "knowledge", "title": "Monday vs ClickUp", "url": "https://example.com/monday-vs-clickup", "metadata": {"site_domain": "example.com"}}]}}),
          ("content_block_stop", {"type": "content_block_stop", "index": 2}),
          ("content_block_start", {"type": "content_block_start", "index": 3, "content_block": {"type": "text", "text": ""}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 3, "delta": {"type": "text_delta", "text": "ClickUp is a common pick."}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 3, "delta": {"type": "citation_start_delta", "citation": {"url": "https://zapier.com/blog/best-project-management-software/", "title": "Best PM software"}}}),
          ("message_stop", {"type": "message_stop"})]
(OUT / "claude_synthetic.sse.txt").write_text(sse(claude))

# ------------------------------------------------------------ Perplexity (snapshot blocks)
web_results = [{"name": "Best PM software", "url": "https://zapier.com/blog/best-project-management-software/", "snippet": "We tested...", "timestamp": "2026-08-01"},
               {"name": "Agency PM", "url": "https://www.teamwork.com/blog/agency-project-management-software/", "snippet": "Built for client work"},
               {"name": "G2 grid", "url": "https://www.g2.com/categories/project-management", "snippet": "Reviews"}]
pplx = [(None, {"blocks": [{"intended_usage": "plan", "plan_block": {"goals": [{"description": "Searching for agency PM tools"}],
                                                                      "steps": [{"step_type": "SEARCH_WEB", "search_web_content": {"queries": [{"query": "agency project management tools"}, {"query": "ClickUp vs Teamwork agencies"}]}}]}}]}),
        (None, {"blocks": [{"intended_usage": "web_results", "web_result_block": {"web_results": web_results}}]}),
        (None, {"blocks": [{"intended_usage": "ask_text", "markdown_block": {"answer": "ClickUp [1] and Teamwork.com [2] are popular."}}]})]
(OUT / "perplexity_synthetic.sse.txt").write_text(sse(pplx))

# ------------------------------------------------------------ Grok (NDJSON)
grok = [{"result": {"response": {"token": "Looking up tools", "isThinking": True}}},
        {"result": {"response": {"toolUsageCard": {"webSearch": {"query": "best agency project management tools"}}}}},
        {"result": {"response": {"webSearchResults": {"results": [
            {"url": "https://zapier.com/blog/best-project-management-software/", "title": "Best PM software", "preview": "We tested..."},
            {"url": "https://www.capterra.com/project-management-software/", "title": "Capterra PM", "preview": "..."}]}}}},
        {"result": {"response": {"token": "ClickUp is popular."}}},
        {"result": {"response": {"citedWebSearchResults": [{"url": "https://zapier.com/blog/best-project-management-software/", "title": "Best PM software"}]}}}]
(OUT / "grok_synthetic.ndjson").write_text("\n".join(json.dumps(x) for x in grok) + "\n")

# ------------------------------------------------------------ Gemini (batchexecute)
inner = json.dumps([None, ["c_1", "r_1"], None, None,
                    [["rc_1", ["ClickUp and Teamwork.com are good choices for small agencies."], None,
                      [[["https://zapier.com/blog/best-project-management-software/", "Best PM software"]],
                       [["https://www.teamwork.com/blog/agency-project-management-software/", "Agency PM"]]]]]])
chunk = json.dumps([["wrb.fr", None, inner]])
(OUT / "gemini_synthetic.txt").write_text(")]}'\n\n" + f"{len(chunk)}\n{chunk}\n")
print("wrote synthetic fixtures to", OUT)
