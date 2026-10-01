"""Offline tests: real PDF parsing + fixture analyzer, no network/account needed."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.papers.analyzers import CodexAnalyzer, PaperAnalyzer
from tools.papers.ingest import main as cli_main
from tools.papers.parsers import PyMuPDFParser
from tools.papers.service import PaperService, validate_grounding
from tools.papers.storage import PaperStore, choose_id, encode, atomic_write, validate
from tools.papers.zotero import ZoteroAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def fixture() -> dict:
    return json.loads((FIXTURES / "paper.json").read_text())


def fake_codex(directory: Path) -> Path:
    executable = directory / "fake-codex"
    atomic_write(executable, (FIXTURES / "fake_codex.py").read_bytes())
    executable.chmod(0o700)
    atomic_write(directory / "paper.json", encode(fixture()))
    return executable


def make_pdf(path: Path, *, with_image: bool = False):
    import pymupdf
    from PIL import Image, ImageDraw
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(40, 30, 555, 500), (FIXTURES / "paper.txt").read_text(), fontsize=11)
    if with_image:
        image = Image.new("RGB", (500, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 30, 150, 140), outline="black", width=3)
        draw.rectangle((330, 30, 480, 140), outline="black", width=3)
        draw.line((150, 85, 330, 85), fill="black", width=3)
        draw.text((30, 65), "PDF Parser", fill="black")
        draw.text((350, 65), "Schema JSON", fill="black")
        png = io.BytesIO()
        image.save(png, format="PNG")
        page.insert_image(pymupdf.Rect(50, 520, 550, 720), stream=png.getvalue())
        page.insert_text((50, 750), "Figure 1: Synthetic parser to JSON pipeline.", fontsize=10)
    document.save(path)
    document.close()


class FixtureAnalyzer(PaperAnalyzer):
    name = "fixture"

    def __init__(self, fail=False, figures=False):
        self.calls = 0
        self.fail = fail
        self.figures = figures
        self.contexts = []

    def analyze(self, parsed, metadata, task_dir):
        self.calls += 1
        self.contexts.append((copy.deepcopy(parsed), str(task_dir)))
        if self.fail:
            raise RuntimeError("Fixture analyzer failed")
        paper = fixture()
        paper["analysis_meta"] = metadata["analysis_meta"]
        if self.figures:
            candidate = parsed["figure_candidates"][0]
            paper["figures"] = [{"id": candidate["id"], "page": candidate["page"], "caption": candidate["caption"],
                                "description": "Synthetic method diagram", "path": "", "type": "method_overview"}]
        return paper


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="paper-tests-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "fixture.pdf"
        make_pdf(self.source)
        self.analyzer = FixtureAnalyzer()
        self.service = PaperService(self.analyzer, parser=PyMuPDFParser(), root=self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_pdf_to_json_index_and_queries(self):
        paper = self.service.ingest(self.source)
        validate(paper)
        self.assertEqual(paper["id"], "fixture-2026")
        self.assertEqual(self.analyzer.calls, 1)
        self.assertEqual(paper['tags'], [])  # Ignore tags even if an analyzer emits them.
        row = self.service.list_papers()[0]
        self.assertNotIn("evidence", row)
        self.assertNotIn("method", row)
        self.assertEqual(len(self.service.search_papers("Example Author", "testing", 2026)), 0)
        from tools.papers.tags import PaperTagService
        PaperTagService(self.root).save({"version": "1.0", "tags": [{"id": "t-testing", "name": "testing"}],
                                      "papers": {paper['id']: ["t-testing"]}})
        self.assertEqual(len(self.service.search_papers("Example Author", "testing", 2026)), 1)
        self.assertEqual(self.service.get_paper(paper["id"])["id"], paper["id"])
        self.assertFalse(list((self.root / "data").rglob("*.pdf")))
        self.assertNotIn(str(self.root), (self.root / "data/papers/fixture-2026.json").read_text())

    def test_cache_update_force_and_no_implicit_overwrite(self):
        first = self.service.ingest(self.source)
        with self.assertRaisesRegex(ValueError, "--update"):
            self.service.ingest(self.source)
        second = self.service.ingest(self.source, update=True)
        self.assertEqual(self.analyzer.calls, 1)
        self.assertEqual(first["analysis_meta"], second["analysis_meta"])
        third = self.service.ingest(self.source, update=True, force=True)
        self.assertEqual(self.analyzer.calls, 2)
        self.assertEqual(third["id"], first["id"])
        self.assertNotEqual(self.analyzer.contexts[0][1], self.analyzer.contexts[1][1])

    def test_deterministic_rebuild(self):
        self.service.ingest(self.source)
        self.service.rebuild_index()
        before = (self.root / "data/papers/index.json").read_bytes()
        self.service.rebuild_index()
        self.assertEqual(before, (self.root / "data/papers/index.json").read_bytes())

    def test_failed_analysis_preserves_library(self):
        self.service.ingest(self.source)
        before = {p.name: p.read_bytes() for p in (self.root / "data/papers").glob("*.json")}
        self.analyzer.fail = True
        with self.assertRaises(RuntimeError):
            self.service.ingest(self.source, update=True, force=True, retries=0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / "data/papers").glob("*.json")})
        self.assertTrue(list(self.service.cache.rglob("last-error.log")))

    def test_invalid_json_and_asset_failure_never_publish(self):
        self.service.ingest(self.source)
        before = {p.name: p.read_bytes() for p in (self.root / "data/papers").glob("*.json")}
        invalid = fixture()
        invalid["extra_report"] = "Not permitted"
        with patch.object(self.analyzer, "analyze", return_value=invalid):
            with self.assertRaisesRegex(ValueError, "Schema validation"):
                self.service.ingest(self.source, update=True, force=True, retries=0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / "data/papers").glob("*.json")})
        make_pdf(self.source, with_image=True)
        self.analyzer.figures = True
        with patch.object(self.service.parser, "extract_figure", side_effect=RuntimeError("Image failed")):
            with self.assertRaises(RuntimeError):
                self.service.ingest(self.source, update=True, force=True, retries=0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / "data/papers").glob("*.json")})

    def test_crash_journal_recovery(self):
        self.service.ingest(self.source)
        store = self.service.store
        index = store.data / "index.json"
        original = index.read_bytes()
        import base64
        atomic_write(store.journal, encode([{"path": "data/papers/index.json", "previous": base64.b64encode(original).decode()}]))
        atomic_write(index, b"interrupted output")
        self.service.list_papers()  # Any next service read acquires lock and recovers.
        self.assertEqual(original, index.read_bytes())
        self.assertFalse(store.journal.exists())

    def test_pdf_parse_failure_does_not_create_index(self):
        atomic_write(self.source, b"not a PDF")
        with self.assertRaises(Exception):
            self.service.ingest(self.source, retries=0)
        self.assertFalse((self.root / "data/papers/index.json").exists())

    def test_source_change_during_analysis_cancels_publication(self):
        original_analyze = self.analyzer.analyze
        def analyze_and_change(parsed, metadata, task_dir):
            result = original_analyze(parsed, metadata, task_dir)
            atomic_write(self.source, self.source.read_bytes() + b"\nchanged during analysis\n")
            return result
        with patch.object(self.analyzer, "analyze", side_effect=analyze_and_change):
            with self.assertRaisesRegex(ValueError, "changed while analyzing"):
                self.service.ingest(self.source, retries=0)
        self.assertFalse((self.root / "data/papers/index.json").exists())

    def test_related_matching_when_target_added_later(self):
        self.service.ingest(self.source)
        companion = fixture()
        companion["id"] = "companion-2025"
        companion["metadata"].update(title="Companion Fixture Study", year=2025)
        companion["analysis_meta"]["source_hash"] = "1" * 64
        self.service.store.publish(companion, {})
        self.assertEqual(self.service.related_papers("fixture-2026")[0]["paper_id"], "companion-2025")

    def test_strict_schema_privacy_urls_and_refs(self):
        for mutate in (lambda p: p.update(secret="bad"),
                       lambda p: p["metadata"].update(paper_url="javascript:alert(1)"),
                       lambda p: p["metadata"].update(pdf_url="file:///private/paper.pdf"),
                       lambda p: p["overview"].update(core_idea="/Users/example/private"),
                       lambda p: p["contributions"][0].update(evidence_ids=["missing"])):
            paper = fixture()
            mutate(paper)
            with self.assertRaises(ValueError):
                validate(paper)

    def test_evidence_must_exist_on_page(self):
        paper = fixture()
        parsed = PyMuPDFParser().parse(self.source)
        validate_grounding(paper, parsed, {})
        paper["evidence"][0]["content"] = "Invented scientific result"
        with self.assertRaisesRegex(ValueError, "contiguous excerpt"):
            validate_grounding(paper, parsed, {})

    def test_stable_ids_and_conflicts(self):
        record = fixture()
        record["metadata"]["doi"] = "10.0000/fixture"
        metadata = copy.deepcopy(record["metadata"])
        metadata["short_title"] = "A corrected title"
        self.assertEqual(choose_id(metadata, [record], "1" * 64), record["id"])
        metadata["doi"] = "10.0000/different"
        metadata["short_title"] = "Fixture"
        new = choose_id(metadata, [record], "2" * 64)
        self.assertNotEqual(new, record["id"])
        with self.assertRaises(ValueError):
            choose_id(metadata, [record], "2" * 64, record["id"])
        metadata.update(short_title="index", year=None)
        self.assertNotEqual(choose_id(metadata, [], "2" * 64), "index")
        with self.assertRaises(ValueError):
            choose_id(metadata, [], "2" * 64, "index")

    def test_assets_selected_only_and_cleanup(self):
        make_pdf(self.source, with_image=True)
        self.analyzer.figures = True
        paper = self.service.ingest(self.source)
        self.assertEqual(len(paper["figures"]), 1)
        asset = self.root / "public" / paper["figures"][0]["path"].lstrip("/")
        self.assertTrue(asset.read_bytes().startswith(b"RIFF"))
        self.analyzer.figures = False
        self.service.ingest(self.source, force=True, update=True)
        self.assertFalse(asset.exists())

    def test_atomic_publication_rolls_back_and_recovers(self):
        self.service.ingest(self.source)
        store = self.service.store
        original = (store.data / "fixture-2026.json").read_bytes()
        real_write = atomic_write
        failed = False
        def failing_write(path, content):
            nonlocal failed
            if path.name == "index.json" and not failed:
                failed = True
                raise OSError("Simulated write failure")
            return real_write(path, content)
        paper = self.service.get_paper("fixture-2026")
        paper["overview"]["one_sentence"] = "Updated"
        with patch("tools.papers.storage.atomic_write", side_effect=failing_write):
            with self.assertRaises(OSError):
                store.publish(paper, {}, update=True)
        self.assertEqual(original, (store.data / "fixture-2026.json").read_bytes())
        self.assertFalse(store.journal.exists())

    def test_codex_fresh_process_contract_and_context(self):
        analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)), keep_logs=True)
        with patch.dict(os.environ, {"ZOTERO_API_KEY": "secret-test-only", "CODEX_SESSION_ID": "parent-context"}):
            analyzer.analyze({"pages": [{"text": "PAPER_A_ONLY"}]}, {}, self.root / "task-a")
            analyzer.analyze({"pages": [{"text": "PAPER_B_ONLY"}]}, {}, self.root / "task-b")
        first, second = [json.loads((self.root / name / "received.json").read_text()) for name in ("task-a", "task-b")]
        commands, prompts = [r["args"] for r in (first, second)], [r["prompt"] for r in (first, second)]
        for command in commands:
            self.assertIn("--ephemeral", command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("--output-schema", command)
            self.assertIn('history.persistence="none"', command)
            self.assertNotIn("resume", command)
            self.assertNotIn("fork", command)
            self.assertIn('model_reasoning_effort="high"', command)
            self.assertEqual(command[command.index("--model") + 1], "gpt-6.1-sol")
        self.assertNotIn("PAPER_A_ONLY", prompts[1])
        self.assertNotEqual(commands[0], commands[1])
        self.assertFalse(first["has_zotero_key"])
        self.assertFalse(second["has_parent_session"])

    def test_codex_configuration_defaults_overrides_and_cache(self):
        with patch.dict(os.environ, {"PAPER_CODEX_MODEL": "", "PAPER_CODEX_REASONING_EFFORT": ""}):
            analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)))
        self.assertEqual((analyzer.model, analyzer.reasoning_effort), ("gpt-6.1-sol", "high"))
        high = analyzer.cache_identity()
        with patch.dict(os.environ, {"PAPER_CODEX_MODEL": "env-model", "PAPER_CODEX_REASONING_EFFORT": "medium"}):
            inherited = CodexAnalyzer(binary=analyzer.binary)
            explicit = CodexAnalyzer(binary=analyzer.binary, model="explicit-model", reasoning_effort="max")
        self.assertEqual((inherited.model, inherited.reasoning_effort), ("env-model", "medium"))
        self.assertEqual((explicit.model, explicit.reasoning_effort), ("explicit-model", "max"))
        medium = CodexAnalyzer(binary=analyzer.binary, reasoning_effort="medium").cache_identity()
        self.assertNotEqual(high, medium)
        with self.assertRaises(ValueError):
            CodexAnalyzer(reasoning_effort="none")
        with self.assertRaises(ValueError):
            CodexAnalyzer(heartbeat_interval=0)

    def test_custom_codex_home_headers_and_wait_heartbeat(self):
        custom_home = self.root / "custom-codex"
        atomic_write(custom_home / "config.toml", b'cli_auth_credentials_store="keyring"\nmodel_reasoning_effort="low"\n')
        messages = []
        with patch.dict(os.environ, {"CODEX_HOME": str(custom_home), "PAPER_TEST_DELAY": "0.18",
                                   "PAPER_CODEX_MODEL": "", "PAPER_CODEX_REASONING_EFFORT": "", "PAPER_CODEX_AUTH_STORE": ""}):
            analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)), heartbeat_interval=.05, progress=messages.append,
                                     keep_logs=True)
            analyzer.analyze({"pages": []}, {}, self.root / "heartbeat-task")
        received = json.loads((self.root / "heartbeat-task/received.json").read_text())
        self.assertEqual(received["codex_home"], str(custom_home.resolve()))
        self.assertEqual(analyzer.auth_store, "keyring")
        self.assertIn('cli_auth_credentials_store="keyring"', received["args"])
        self.assertTrue(any("子进程尚未退出" in message for message in messages))
        self.assertTrue(any("reasoning effort=high" in message for message in messages))
        self.assertTrue(any(str(custom_home.resolve()) in message for message in messages))

    def test_codex_timeout_stops_child_and_preserves_task_logs(self):
        with patch.dict(os.environ, {"PAPER_TEST_DELAY": "30"}):
            analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)), timeout=.2, heartbeat_interval=.05, keep_logs=True)
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                analyzer.analyze({"pages": []}, {}, self.root / "timeout-task")
        self.assertLess(time.monotonic() - started, 4)
        self.assertFalse((self.root / "timeout-task/output.json").exists())
        self.assertTrue((self.root / "timeout-task/stderr.log").exists())

    def test_codex_keyboard_interrupt_stops_its_fresh_process(self):
        from unittest.mock import Mock
        process = Mock(pid=999999, returncode=None)
        process.communicate.side_effect = [KeyboardInterrupt(), (None, None)]
        analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)))
        analyzer.check()
        with patch("tools.papers.analyzers.codex.subprocess.Popen", return_value=process), \
                patch("tools.papers.analyzers.codex.os.killpg") as stop_group:
            with self.assertRaises(KeyboardInterrupt):
                analyzer.analyze({"pages": []}, {}, self.root / "interrupted-task")
        self.assertEqual(stop_group.call_count, 2)
        process.wait.assert_called_once()
        self.assertFalse((self.root / "interrupted-task").exists())

    def test_codex_default_removes_successful_task_but_returns_json(self):
        with patch.dict(os.environ, {"PAPER_KEEP_ANALYSIS_LOGS": "0"}):
            analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)))
        task = self.root / "auto-clean-task"
        result = analyzer.analyze({"pages": []}, {}, task)
        self.assertEqual(result["metadata"]["title"], fixture()["metadata"]["title"])
        self.assertFalse(task.exists())

    def test_codex_default_removes_timed_out_task(self):
        with patch.dict(os.environ, {"PAPER_KEEP_ANALYSIS_LOGS": "0", "PAPER_TEST_DELAY": "30"}):
            analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)), timeout=.1, heartbeat_interval=.05)
            task = self.root / "auto-clean-timeout"
            with self.assertRaises(TimeoutError):
                analyzer.analyze({"pages": []}, {}, task)
        self.assertFalse(task.exists())

    def test_codex_retention_opt_in_and_existing_directory_protection(self):
        with patch.dict(os.environ, {"PAPER_KEEP_ANALYSIS_LOGS": "1"}):
            analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)))
        task = self.root / "explicit-debug"
        analyzer.analyze({"pages": []}, {}, task)
        self.assertTrue((task / "prompt.txt").exists())
        self.assertTrue((task / "stderr.log").exists())
        self.assertTrue((task / "output.json").exists())
        cleaner = CodexAnalyzer(binary=analyzer.binary, keep_logs=False)
        with self.assertRaises(FileExistsError):
            cleaner.analyze({"pages": []}, {}, task)
        self.assertTrue((task / "output.json").exists())

    def test_service_cleanup_preserves_cache_and_short_failure_record(self):
        with patch.dict(os.environ, {"PAPER_KEEP_ANALYSIS_LOGS": "0"}):
            analyzer = CodexAnalyzer(binary=str(fake_codex(self.root)))
        service = PaperService(analyzer, parser=PyMuPDFParser(), root=self.root)
        paper = service.ingest(self.source, retries=0)
        self.assertTrue((service.store.data / f"{paper['id']}.json").exists())
        self.assertTrue(list(service.cache.rglob("analysis-*.json")))
        self.assertTrue(list(service.cache.rglob("parsed-*.json")))
        self.assertFalse(list(service.cache.rglob("prompt.txt")))
        self.assertFalse(list(service.cache.rglob("stdout.log")))
        with patch.object(analyzer, "analyze", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                service.ingest(self.source, update=True, force=True, retries=0)
        error = next(service.cache.rglob("last-error.log")).read_text()
        self.assertEqual(error, "RuntimeError: synthetic failure\n")
        self.assertTrue((service.store.data / f"{paper['id']}.json").exists())

    def test_cli_displays_stages_and_effective_codex_settings(self):
        from contextlib import redirect_stdout, redirect_stderr
        executable = fake_codex(self.root)
        stdout, stderr = io.StringIO(), io.StringIO()
        def local_service(**kwargs):
            return PaperService(root=self.root, parser=PyMuPDFParser(), **kwargs)
        with patch.dict(os.environ, {"PAPER_CODEX_BIN": str(executable), "PAPER_CODEX_MODEL": "", "PAPER_CODEX_REASONING_EFFORT": ""}), \
                patch("tools.papers.ingest.PaperService", side_effect=local_service), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main([str(self.source), "--retries", "0"])
        self.assertEqual(code, 0)
        for stage in range(1, 7):
            self.assertIn(f"[{stage}/6]", stderr.getvalue())
        self.assertIn("model=gpt-6.1-sol", stderr.getvalue())
        self.assertIn("reasoning_effort=high", stderr.getvalue())
        self.assertIn("CODEX_HOME=", stderr.getvalue())
        self.assertIn("Published: fixture-2026", stdout.getvalue())

    def test_zotero_readonly_metadata_and_local_attachment(self):
        directory = self.root / "zotero/ATTACH01"
        directory.mkdir(parents=True)
        source = directory / "paper.pdf"
        make_pdf(source)
        with patch.dict(os.environ, {"ZOTERO_STORAGE_DIR": str(self.root / "zotero")}):
            adapter = ZoteroAdapter()
        item = {"key": "PARENT01", "library": {"id": 123}, "data": {"title": "Reliable Zotero title", "date": "2024-08-01",
                "DOI": "10.0000/test", "creators": [{"creatorType": "author", "firstName": "A", "lastName": "Author"}], "collections": ["COLL0001"], "tags": [{"tag": "test"}]}}
        child = {"key": "ATTACH01", "data": {"itemType": "attachment", "contentType": "application/pdf", "filename": "paper.pdf"}}
        with patch.object(adapter, "_get", side_effect=[item, [child]]):
            path, metadata = adapter.resolve("PARENT01")
        self.assertEqual(path.resolve(), source.resolve())
        self.assertEqual(metadata["year"], 2024)
        self.assertEqual(metadata["zotero"]["item_key"], "PARENT01")
        self.assertNotIn(str(source), json.dumps(metadata))

    def test_cache_not_publishable(self):
        with self.assertRaises(ValueError):
            PaperService(root=self.root, cache_dir=self.root / "public/unsafe-cache")


if __name__ == "__main__":
    unittest.main()
