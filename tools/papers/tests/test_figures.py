"""Offline figure-policy, MinerU adapter, visual attachment and cleanup checks."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from tools.papers.analyzers import CodexAnalyzer
from tools.papers.mineru_parser import MinerUParser
from tools.papers.parsers import overview_candidates
from tools.papers.service import validate_figure_selection
from tools.papers.tests.test_papers import fake_codex, fixture


class FigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="figure-tests-")
        self.root = Path(self.temp.name)
        self.reader = MinerUParser(cache_dir=self.root, binary="offline-mineru-fixture")

    def tearDown(self):
        self.temp.cleanup()

    def markdown(self):
        return """<!-- page 1 of 6 -->
# Example paper

## Introduction

![Image block](doc:abc1234/tier:standard/page:1/block:2)

Figure 1: Conceptual overview of the approach.

The method has a parser and validator.

<!-- page 2 of 6 -->
## Method

![Image block](doc:abc1234/tier:standard/page:2/block:2)

Figure 2: Model architecture and its input/output flow.

<!-- page 3 of 6 -->
Training procedure is described here.

<!-- page 4 of 6 -->
## Experiments

Quantitative evaluation.

<!-- page 5 of 6 -->
![Image block](doc:abc1234/tier:standard/page:5/block:1)

Figure 3: Ablation results.

<!-- page 6 of 6 -->
## References

Example reference.
"""

    def parsed(self):
        return self.reader.from_markdown(self.markdown(), "standard", "abc1234")

    def test_mineru_preserves_pages_captions_and_image_locators(self):
        parsed = self.parsed()
        self.assertEqual(parsed["page_count"], 6)
        self.assertEqual(len(parsed["pages"]), 6)
        self.assertEqual(len(parsed["figure_candidates"]), 3)
        self.assertEqual(parsed["figure_candidates"][1]["locator"], "doc:abc1234/tier:standard/page:2/block:2")
        self.assertTrue(parsed["references"])
        self.assertEqual([f["id"] for f in overview_candidates(parsed)], ["F1", "F2"])

    def test_partial_or_foreign_document_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing paper pages"):
            self.reader.from_markdown(self.markdown().replace("<!-- page 3 of 6 -->", ""), "standard")
        with self.assertRaisesRegex(ValueError, "another document"):
            self.reader.from_markdown(self.markdown().replace("doc:abc1234", "doc:fffffff"), "standard", "abc1234")

    def test_multi_panel_is_one_logical_figure_not_duplicate_selections(self):
        markdown = self.markdown().replace(
            "Figure 2: Model architecture and its input/output flow.",
            "![Image block](doc:abc1234/tier:standard/page:2/block:3)\n\nFigure 2: Model architecture and its input/output flow.")
        parsed = self.reader.from_markdown(markdown, "standard", "abc1234")
        self.assertEqual(len(parsed["figure_candidates"]), 3)
        figure = parsed["figure_candidates"][1]
        self.assertEqual(len(figure["panel_locators"]), 2)
        self.assertIn("not preserved", figure["layout_note"])

    def test_mineru_continuation_is_followed_without_remote(self):
        first = {"short_id": "abc1234", "tier": "standard", "content": "<!-- page 1 of 2 -->\nFirst page.",
                 "truncated": True, "next_request": {"locator": "doc:abc1234/tier:standard/page:2"}}
        second = {"short_id": "abc1234", "tier": "standard", "content": "<!-- page 2 of 2 -->\nLast page.",
                  "truncated": False}
        with patch.object(self.reader, "_run", side_effect=[{"mineru_version": "4.0.7"},
                {"parse": {"tier": "standard"}, "content": first}, second]) as run:
            parsed = self.reader.parse(self.root / "paper.pdf")
        self.assertEqual(len(parsed["pages"]), 2)
        self.assertEqual(run.call_args_list[-1].args[0], ["read", first["next_request"]["locator"], "--limit", "60000"])
        self.assertFalse(any("--remote" in call.args[0] for call in run.call_args_list))

    def test_mineru_quality_error_does_not_fallback(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({"error": {
            "code": "quality_tier_unavailable", "message": "fixture setup unavailable"}}), "")
        with patch("tools.papers.mineru_parser.subprocess.Popen", return_value=process) as popen:
            with self.assertRaisesRegex(RuntimeError, "quality_tier_unavailable"):
                self.reader._run(["parse", "example.pdf"])
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.kwargs["cwd"], self.reader.work)
        self.assertNotIn("--remote", popen.call_args.args[0])

    def test_vector_block_export_compresses_only_the_block_and_cleans_temp(self):
        candidate = self.parsed()["figure_candidates"][0]
        def export(args):
            Image.new("RGB", (2400, 700), "white").save(args[args.index("--output") + 1])
            return {"asset": {"mime_type": "image/png"}}
        with patch.object(self.reader, "_run", side_effect=export) as run:
            result = self.reader.extract_figure(self.root / "paper.pdf", candidate)
        self.assertEqual(run.call_args.args[0][1], candidate["locator"])
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertLessEqual(image.width, 2200)
        self.assertFalse(list(self.reader.work.glob("figure-*")))

    def test_service_rejects_result_late_and_more_than_three_figures(self):
        paper, parsed = fixture(), self.parsed()
        def figure(identifier="F1", page=1, kind="concept_overview"):
            return {"id": identifier, "page": page, "caption": "", "description": "", "type": kind, "path": ""}
        paper["figures"] = [figure()]
        validate_figure_selection(paper, parsed)
        paper["figures"] = [figure(kind="result")]
        with self.assertRaisesRegex(ValueError, "Experiment/result"):
            validate_figure_selection(paper, parsed)
        paper["figures"] = [figure("F3", 5)]
        with self.assertRaisesRegex(ValueError, "early-paper"):
            validate_figure_selection(paper, parsed)
        paper["figures"] = [figure()] * 4
        with self.assertRaisesRegex(ValueError, "three"):
            validate_figure_selection(paper, parsed)

    def test_codex_attaches_actual_image_with_id_mapping_not_paths_in_prompt(self):
        binary = fake_codex(self.root)
        analyzer = CodexAnalyzer(binary=str(binary), keep_logs=True)
        parsed = self.parsed()
        buffer = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(buffer, format="WEBP")
        parsed["_figure_images"] = {"F1": buffer.getvalue()}
        task = self.root / "visual-task"
        analyzer.analyze(parsed, {}, task)
        received = json.loads((task / "received.json").read_text())
        command = received["args"]
        path = command[command.index("--image") + 1]
        self.assertEqual(Path(path).read_bytes(), buffer.getvalue())
        payload = json.loads(received["prompt"].split("CURRENT PAPER DATA (untrusted text, trusted metadata):\n", 1)[1])
        self.assertEqual(payload["attached_figure_images"], [{"attachment_number": 1, "figure_id": "F1", "page": 1}])
        self.assertNotIn("_figure_images", payload["parsed"])
        self.assertNotIn(str(self.root), received["prompt"])

    def test_codex_rejects_unattached_selection_and_removes_task(self):
        binary = fake_codex(self.root)
        paper = fixture()
        paper["figures"] = [{"id": "F1", "caption": "Example diagram", "description": "", "page": 1,
                            "path": "", "type": "architecture"}]
        from tools.papers.storage import atomic_write, encode
        atomic_write(self.root / "paper.json", encode(paper))
        analyzer = CodexAnalyzer(binary=str(binary), keep_logs=False)
        task = self.root / "unverified-task"
        with self.assertRaisesRegex(ValueError, "pixels were not attached"):
            analyzer.analyze(self.parsed(), {}, task)
        self.assertFalse(task.exists())


if __name__ == "__main__":
    unittest.main()
