"""Opt-in local PDF/MinerU image audit. No Codex calls or official paper writes."""
import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.papers.mineru_parser import MinerUParser
from tools.papers.parsers import overview_candidates
from tools.papers.progress import console_progress
from tools.papers.service import file_hash
from tools.papers.storage import ROOT, atomic_write, encode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    source = args.pdf.expanduser().resolve(strict=True)
    directory = ROOT / ".cache/papers/figure-audit" / file_hash(source)
    reader = MinerUParser(cache_dir=directory, progress=console_progress)
    parsed = reader.parse(source)
    atomic_write(directory / "parsed.json", encode(parsed))
    candidates = overview_candidates(parsed)
    for figure in candidates:
        path = directory / f"{figure['id']}.webp"
        atomic_write(path, reader.extract_figure(source, figure))
        print(f"{figure['id']} · PDF p.{figure['page']} · {figure['locator']} → {path}", flush=True)
    print(f"LOCAL ONLY: {len(parsed['pages'])} pages; {len(parsed['figure_candidates'])} figure blocks; "
          f"{len(candidates)} early overview candidates; no source PDF copied, no official JSON published.")


if __name__ == "__main__":
    main()
