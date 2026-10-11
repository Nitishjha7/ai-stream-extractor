"""Run:  python -m unittest discover -s tests   (fixtures: python samples/make_samples.py)"""
import json, unittest
from pathlib import Path
from extractor.cli import run
from extractor.platforms.chatgpt import _apply

S = Path(__file__).parent.parent / "samples"


def one(name):
    [ex] = run(S / name)
    return ex.to_dict()


class DeltaReplay(unittest.TestCase):
    def test_ops(self):
        doc = _apply(None, {"p": "", "o": "add", "v": {"message": {"content": {"parts": [""]}}}})
        doc = _apply(doc, {"p": "/message/content/parts/0", "o": "append", "v": "Hel"})
        doc = _apply(doc, {"o": "patch", "p": "", "v": [
            {"p": "/message/content/parts/0", "o": "append", "v": "lo"},
            {"p": "/message/metadata/x", "o": "add", "v": 1}]})
        self.assertEqual(doc["message"]["content"]["parts"][0], "Hello")
        self.assertEqual(doc["message"]["metadata"]["x"], 1)
        doc = _apply(doc, {"p": "/message/content/parts/0", "o": "truncate", "v": 2})
        self.assertEqual(doc["message"]["content"]["parts"][0], "He")


class ChatGPT(unittest.TestCase):
    def setUp(self):
        self.d = one("chatgpt_synthetic.sse.txt")

    def test_queries(self):
        self.assertEqual(len(self.d["fanout_queries"]), 4)

    def test_retrieved_vs_cited(self):
        by = {s["url"].split("//")[1][:20]: s for s in self.d["sources"]}
        reddit = next(s for s in self.d["sources"] if "reddit" in s["url"])
        self.assertTrue(reddit["retrieved"]); self.assertFalse(reddit["cited"])
        self.assertEqual(self.d["summary"]["n_cited"], 3)

    def test_utm_dedupe_and_video(self):
        vids = [s for s in self.d["sources"] if s["is_video"]]
        self.assertEqual(len(vids), 1)
        self.assertEqual(vids[0]["video"]["views"], 15234)
        self.assertIn("01:12", vids[0]["snippet"])  # description timecodes survive

    def test_answer_and_reasoning(self):
        self.assertIn("ClickUp", self.d["answer_text"])
        self.assertTrue(self.d["reasoning"])
        self.assertEqual(self.d["prompt"], "best project management tools for small agencies")


class Others(unittest.TestCase):
    def test_claude(self):
        d = one("claude_synthetic.sse.txt")
        self.assertEqual(d["fanout_queries"], ["agency project management tools 2026"])
        self.assertEqual(d["summary"]["n_cited"], 1)

    def test_perplexity(self):
        d = one("perplexity_synthetic.sse.txt")
        self.assertIn("ClickUp vs Teamwork agencies", d["fanout_queries"])
        g2 = next(s for s in d["sources"] if "g2.com" in s["url"])
        self.assertFalse(g2["cited"])

    def test_grok(self):
        d = one("grok_synthetic.ndjson")
        self.assertEqual(d["summary"]["n_cited"], 1)
        self.assertTrue(d["reasoning"])

    def test_gemini(self):
        d = one("gemini_synthetic.txt")
        self.assertEqual(d["summary"]["n_sources_total"], 2)


if __name__ == "__main__":
    unittest.main()
