"""Optional real Codex smoke; --browser-assets explicitly exports one test image."""
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.papers.analyzers import CodexAnalyzer
from tools.papers.service import PaperService
from tools.papers.parsers import PyMuPDFParser
from tools.papers.progress import console_progress
from tools.papers.storage import ROOT, atomic_write, encode
from tools.papers.tags import PaperTagService
from tools.papers.tests.test_papers import make_pdf, fixture, FixtureAnalyzer


def main():
    live = "--live" in sys.argv
    if live and "--browser-assets" in sys.argv:
        raise ValueError("--browser-assets is only for the offline synthetic fixture")
    if "--clean-browser-assets" in sys.argv:
        # Remove ONLY the exact test asset exported by this helper, after byte
        # verification against its cached original. Never scan/delete real assets.
        generated = ROOT / ".cache/papers/smoke-site/public/paper-assets/fixture-2026/f1.webp"
        public = ROOT / "public/paper-assets/fixture-2026/f1.webp"
        if public.exists():
            if not generated.exists() or public.read_bytes() != generated.read_bytes():
                raise ValueError("Test image changed; refusing cleanup")
            public.unlink()
            if not any(public.parent.iterdir()):
                public.parent.rmdir()
        print("Synthetic browser asset cleaned; real paper assets untouched.")
        return
    root = ROOT / ".cache/papers/smoke-site"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "synthetic-fixture.pdf"
    make_pdf(source, with_image=True)
    analyzer = CodexAnalyzer(timeout=240, progress=console_progress) if live else FixtureAnalyzer(figures=True)
    # Explicit lightweight parser for this synthetic offline fixture; real PDF
    # ingest defaults to installed local MinerU, verified separately.
    service = PaperService(analyzer, parser=PyMuPDFParser(), root=root, progress=console_progress)
    paper = service.ingest(source, update=True, force=True, retries=0)
    # Add a clearly marked companion fixture to test internal relation resolution.
    companion = fixture()
    companion["id"] = "companion-fixture-2025"
    companion["metadata"].update(title="Companion Fixture Study", short_title="Companion fixture", year=2025)
    companion["related_papers"] = [{"title": "Uncollected synthetic reference", "paper_id": None,
        "relation_type": "related", "description": "External link test, not a scientific reference.",
        "source": "external_metadata", "doi": "", "arxiv_id": "", "url": "https://example.org/reference", "evidence_ids": []}]
    companion["tags"] = ["companion"]
    companion["metadata"]["paper_url"] = "https://example.org/"
    service.store.publish(companion, {}, update=True)
    PaperTagService(root).save({"version": "1.0", "tags": [{"id": "t-testing", "name": "testing"}],
                               "papers": {paper['id']: ["t-testing"]}})
    service.rebuild_index()
    # Browser fixtures can reference a public test image, explicitly opt-in. Never
    # export a live-model analysis/PDF or put demo JSON in the official library.
    if "--browser-assets" in sys.argv:
        for figure in paper["figures"]:
            generated = root / "public" / figure["path"].lstrip("/")
            target = ROOT / "public" / figure["path"].lstrip("/")
            if target.exists() and target.read_bytes() != generated.read_bytes():
                raise ValueError("A different public fixture image exists; refusing overwrite")
            atomic_write(target, generated.read_bytes())
    print(f"{'LIVE CODEX' if live else 'OFFLINE FIXTURE'}: PDF → {paper['id']}.json → index ({len(service.list_papers())} records)")
    print("Build fixture pages: PAPER_DATA_DIR=.cache/papers/smoke-site/data/papers npm run build")


if __name__ == "__main__":
    main()
