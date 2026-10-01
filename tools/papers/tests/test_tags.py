"""Manual tags are authoritative; analysis/evidence stay unchanged across edits."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.papers.tags import PaperTagService, normalize_tags
from tools.papers.storage import PaperStore
from tools.papers.service import PaperService
from tools.papers.parsers import PyMuPDFParser
from tools.papers.tests.test_papers import fixture, make_pdf, FixtureAnalyzer


def config():
    return {"version": "1.0", "tags": [{"id": "t-first", "name": "自动驾驶"},
            {"id": "t-second", "name": "规划"}], "papers": {"fixture-2026": ["t-first", "t-second"]}}


class ManualTagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="paper-tags-tests-")
        self.root = Path(self.temp.name)
        self.tags = PaperTagService(self.root)
        self.store = PaperStore(self.root)
        self.store.publish(fixture(), {})

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_ai_tags_are_not_used(self):
        self.assertTrue(self.store.records()[0]['tags'])  # Historical data is untouched.
        self.assertEqual(self.store.rebuild_index()['papers'][0]['tags'], [])

    def test_file_save_multi_tags_index_and_queries(self):
        before = (self.store.data / 'fixture-2026.json').read_bytes()
        result = self.tags.save(config(), expected_revision='missing')
        self.assertEqual(self.tags.get(), result)
        self.assertEqual((self.store.data / 'fixture-2026.json').read_bytes(), before)
        service = PaperService(root=self.root, parser=PyMuPDFParser())
        self.assertEqual(service.get_paper('fixture-2026')['tags'], ['自动驾驶', '规划'])
        self.assertEqual(len(service.search_papers(tag='规划')), 1)
        self.assertEqual(json.loads((self.store.data / 'index.json').read_text())['papers'][0]['tags'], ['自动驾驶', '规划'])

    def test_rename_keeps_assignment_and_delete_removes(self):
        value = config()
        self.tags.save(value)
        value['tags'][0]['name'] = '驾驶模型'
        self.tags.save(value)
        self.assertEqual(self.tags.get()['config']['papers']['fixture-2026'], ['t-first', 't-second'])
        self.assertEqual(self.store.rebuild_index()['papers'][0]['tags'], ['规划', '驾驶模型'])
        value['tags'].pop(0)
        value['papers']['fixture-2026'].remove('t-first')
        self.tags.save(value)
        self.assertEqual(self.store.rebuild_index()['papers'][0]['tags'], ['规划'])

    def test_stale_revision_does_not_overwrite(self):
        first = self.tags.save(config())
        next_config = config()
        next_config['tags'][0]['name'] = '最新名称'
        self.tags.save(next_config, expected_revision=first['revision'])
        before = self.tags.get()
        with self.assertRaisesRegex(ValueError, 'changed elsewhere'):
            self.tags.save(config(), expected_revision=first['revision'])
        self.assertEqual(self.tags.get(), before)

    def test_invalid_configs_preserve_files(self):
        self.tags.save(config())
        before = self.tags.get()
        invalid = []
        for name in ['', ' ', '\nname', 'A' * 61]:
            value = config(); value['tags'][0]['name'] = name; invalid.append(value)
        value = config(); value['tags'][1]['name'] = value['tags'][0]['name']; invalid.append(value)
        value = config(); value['tags'][1]['id'] = value['tags'][0]['id']; invalid.append(value)
        value = config(); value['papers']['fixture-2026'] = ['t-unknown']; invalid.append(value)
        value = config(); value['papers']['fixture-2026'] = ['t-first', 't-first']; invalid.append(value)
        value = config(); value['papers']['../private'] = []; invalid.append(value)
        value = config(); value['secret'] = 'unexpected'; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.tags.save(value)
            self.assertEqual(self.tags.get(), before)

    def test_case_insensitive_unique_and_trim(self):
        value = config(); value['tags'][0]['name'] = '  AI  '; value['tags'][1]['name'] = 'ai'
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            normalize_tags(value)
        value['tags'][1]['name'] = '规划'
        self.assertEqual(normalize_tags(value)['tags'][0]['name'], 'AI')

    def test_deterministic_index_and_config(self):
        first = self.tags.save(config())
        before = self.tags.path.read_bytes(), (self.store.data / 'index.json').read_bytes()
        value = config(); value['tags'].reverse(); value['papers']['fixture-2026'].reverse()
        self.tags.save(value)
        self.assertEqual(before, (self.tags.path.read_bytes(), (self.store.data / 'index.json').read_bytes()))
        self.assertEqual(first['revision'], self.tags.get()['revision'])

    def test_transaction_failure_rolls_back_tags_and_index(self):
        self.tags.save(config())
        before = self.tags.path.read_bytes(), (self.store.data / 'index.json').read_bytes()
        from tools.papers import storage
        original = storage.atomic_write
        def fail_index(path, data):
            if path == self.store.data / 'index.json' and b'changed' in data:
                raise OSError('simulated disk failure')
            return original(path, data)
        value = config(); value['tags'][0]['name'] = 'changed'
        with patch('tools.papers.storage.atomic_write', side_effect=fail_index), self.assertRaises(OSError):
            self.tags.save(value)
        self.assertEqual(before, (self.tags.path.read_bytes(), (self.store.data / 'index.json').read_bytes()))
        self.assertFalse(self.store.journal.exists())

    def test_ingest_update_preserves_manual_tags_and_ignores_supplied_tags(self):
        source = self.root / 'source.pdf'; make_pdf(source)
        service = PaperService(FixtureAnalyzer(), root=self.root, parser=PyMuPDFParser())
        paper = service.ingest(source, update=True, metadata={'tags': ['unwanted']})
        value = config(); value['papers'][paper['id']] = ['t-second']
        self.tags.save(value)
        updated = service.ingest(source, update=True, force=True)
        self.assertEqual(updated['tags'], [])
        self.assertEqual(service.get_paper(paper['id'])['tags'], ['规划'])
        self.assertEqual(len(service.search_papers(tag='unwanted')), 0)


if __name__ == '__main__':
    unittest.main()
