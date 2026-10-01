"""Batch/archive safety tests: all PDFs and generated data stay in temp directories."""
from contextlib import redirect_stdout
import errno
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.papers.batch import archive_pdf, run_batch, run_ingest
from tools.papers.service import file_hash
from tools.papers.storage import ROOT, PaperStore, atomic_write, encode
from tools.papers.tests.test_papers import fixture, fake_codex, make_pdf


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="paper-batch-tests-")
        self.root = Path(self.temp.name).resolve()
        self.incoming = self.root / "new"
        self.ready = self.root / "ready"
        self.incoming.mkdir()
        self.store = PaperStore(self.root)
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def source(self, name="论文 A.pdf"):
        path = self.incoming / name
        atomic_write(path, b"synthetic-pdf-fixture-" + name.encode())
        return path

    def publish(self, source):
        paper = fixture()
        paper["id"] = "fixture-" + file_hash(source)[:8]
        paper["metadata"]["title"] += " " + paper["id"]
        paper["analysis_meta"]["source_hash"] = file_hash(source)
        self.store.publish(paper, {})
        return paper

    def runner(self, source, root, model, effort):
        self.calls.append((source, root, model, effort))
        self.assertTrue(source.exists())
        self.publish(source)
        return 0

    def batch(self, runner=None):
        with redirect_stdout(io.StringIO()):
            return run_batch(self.incoming, self.ready, root=self.root, runner=runner or self.runner)

    def test_sequential_defaults_unicode_spaces_and_fixed_output(self):
        first = self.source("A 论文.PDF")
        second = self.source("b.pdf")
        contents = {p.name: p.read_bytes() for p in (first, second)}
        self.assertEqual(self.batch(), 0)
        self.assertEqual([c[0].name for c in self.calls], [first.name, second.name])
        self.assertTrue(all(c[1:] == (self.root, "gpt-6.1-sol", "high") for c in self.calls))
        for name, content in contents.items():
            self.assertFalse((self.incoming / name).exists())
            self.assertEqual((self.ready / name).read_bytes(), content)
        self.assertEqual(len(self.store.records()), 2)
        self.assertTrue((self.root / "data/papers/index.json").is_file())
        self.assertFalse(list(self.ready.glob("*.json")))

    def test_failed_paper_stays_and_next_paper_continues(self):
        bad, good = self.source("a.pdf"), self.source("b.pdf")
        def runner(source, *args):
            return 1 if source == bad else self.runner(source, *args)
        self.assertEqual(self.batch(runner), 1)
        self.assertTrue(bad.exists())
        self.assertFalse(good.exists())
        self.assertTrue((self.ready / good.name).exists())

    def test_zero_exit_without_publication_never_moves(self):
        source = self.source()
        self.assertEqual(self.batch(lambda *args: 0), 1)
        self.assertTrue(source.exists())
        self.assertFalse(self.ready.exists())

    def test_ready_collision_never_overwrites(self):
        source = self.source()
        self.ready.mkdir()
        atomic_write(self.ready / source.name, b"existing-original")
        digest = file_hash(source)
        self.assertEqual(self.batch(), 0)
        self.assertEqual((self.ready / source.name).read_bytes(), b"existing-original")
        self.assertTrue((self.ready / f"{source.stem}--{digest[:8]}.pdf").is_file())

    def test_resume_verified_publication_without_reanalysis(self):
        source = self.source()
        paper = self.publish(source)
        before = (self.store.data / f"{paper['id']}.json").read_bytes()
        self.assertEqual(self.batch(), 0)
        self.assertEqual(self.calls, [])
        self.assertFalse(source.exists())
        self.assertEqual((self.store.data / f"{paper['id']}.json").read_bytes(), before)

    def test_invalid_index_prevents_archival(self):
        source = self.source()
        self.publish(source)
        atomic_write(self.store.data / "index.json", encode({"papers": []}))
        self.assertEqual(self.batch(), 1)
        self.assertTrue(source.exists())
        self.assertEqual(self.calls, [])

    def test_changed_source_after_ingest_never_moves(self):
        source = self.source()
        def runner(path, *args):
            self.runner(path, *args)
            atomic_write(path, path.read_bytes() + b"changed")
            return 0
        self.assertEqual(self.batch(runner), 1)
        self.assertTrue(source.exists())
        self.assertFalse(self.ready.exists())

    def test_interrupt_stops_queue_and_keeps_source(self):
        first, second = self.source("a.pdf"), self.source("b.pdf")
        runner = Mock(side_effect=KeyboardInterrupt)
        self.assertEqual(self.batch(runner), 130)
        runner.assert_called_once()
        self.assertTrue(first.exists() and second.exists())
        report = json.loads(next((self.root / ".cache/papers/batches").glob("*.json")).read_text())
        self.assertEqual(report["status"], "cancelled")

    def test_non_pdf_subfolders_and_symlinks_ignored(self):
        atomic_write(self.incoming / "readme.txt", b"not a pdf")
        (self.incoming / "subfolder.pdf").mkdir()
        original = self.root / "outside.pdf"
        atomic_write(original, b"external")
        (self.incoming / "linked.pdf").symlink_to(original)
        self.assertEqual(self.batch(), 0)
        self.assertEqual(self.calls, [])
        self.assertTrue(original.exists())

    def test_duplicate_batch_lock(self):
        cache = self.root / ".cache/papers/batches"
        cache.mkdir(parents=True)
        with (cache / ".batch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "已有批量导入"):
                self.batch()

    def test_cross_volume_copy_verified_and_no_overwrite(self):
        source = self.source()
        expected = source.read_bytes()
        with patch("tools.papers.batch.os.link", side_effect=OSError(errno.EXDEV, "cross device")):
            target = archive_pdf(source, self.ready, file_hash(source))
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), expected)

    def test_interrupt_immediately_after_unlink_preserves_archived_original(self):
        source = self.source()
        expected = source.read_bytes()
        unlink = Path.unlink
        def interrupted_unlink(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if path == source:
                raise KeyboardInterrupt
        with patch.object(Path, "unlink", interrupted_unlink):
            with self.assertRaises(KeyboardInterrupt):
                archive_pdf(source, self.ready, file_hash(source))
        self.assertFalse(source.exists())
        self.assertEqual((self.ready / source.name).read_bytes(), expected)

    def test_subprocess_contract_and_cancellation(self):
        source = self.source()
        process = Mock()
        process.wait.side_effect = [KeyboardInterrupt(), KeyboardInterrupt(), 130]
        process.poll.return_value = None
        with patch("tools.papers.batch.subprocess.Popen", return_value=process) as popen, redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                run_ingest(source, self.root, "gpt-6.1-sol", "high")
        command = popen.call_args.args[0]
        self.assertEqual(command, [sys.executable, "-u", str(self.root / "tools/papers/ingest.py"),
                                   str(source), "--model", "gpt-6.1-sol", "--reasoning-effort", "high"])
        self.assertEqual(popen.call_args.kwargs, {"cwd": self.root, "start_new_session": True})
        process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_actual_ingest_subprocess_pdf_to_json_to_archive_offline(self):
        # Copy the existing pipeline into an isolated installation, never into
        # real project data. Only Codex is replaced by the existing fixture CLI.
        shutil.copytree(ROOT / "tools/papers", self.root / "tools/papers", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "schemas").mkdir()
        shutil.copy2(ROOT / "schemas/paper.schema.json", self.root / "schemas/paper.schema.json")
        shutil.copy2(ROOT / "schemas/paper-tags.schema.json", self.root / "schemas/paper-tags.schema.json")
        source = self.incoming / "真实解析 测试.pdf"
        make_pdf(source)
        digest = file_hash(source)
        binary = fake_codex(self.root)
        with patch.dict(os.environ, {"PAPER_CODEX_BIN": str(binary), "PAPER_CACHE_DIR": str(self.root / ".cache/papers"),
                                   "PAPER_TEST_DELAY": "0", "PAPER_PARSER": "pymupdf"}):
            self.assertEqual(self.batch(run_ingest), 0)
        paper = self.store.records()[0]
        self.assertEqual(paper["analysis_meta"]["source_hash"], digest)
        self.assertEqual(paper["analysis_meta"]["analyzer"], "codex")
        self.assertFalse(source.exists())
        self.assertEqual(file_hash(self.ready / source.name), digest)
        self.assertFalse(list(self.store.data.glob("*.pdf")))


if __name__ == "__main__":
    unittest.main()
