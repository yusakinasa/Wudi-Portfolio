#!/usr/bin/env python3
"""python tools/papers/ingest.py [ingest] paper.pdf; ... rebuild-index"""
import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.papers.analyzers import CodexAnalyzer
from tools.papers.analyzers.codex import REASONING_EFFORTS
from tools.papers.progress import console_progress
from tools.papers.service import PaperService


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in {"ingest", "rebuild-index", "-h", "--help"}:
        args.insert(0, "ingest")
    parser = argparse.ArgumentParser(description="Offline Paper Library tools (never copies source PDFs)")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("pdf", type=Path, nargs="?")
    ingest.add_argument("--zotero-item", help="Bibliographic item or PDF attachment key; Zotero is read-only")
    ingest.add_argument("--metadata", type=Path, help="Trusted metadata JSON; no credentials or local paths")
    ingest.add_argument("--id", help="Optional stable slug on first ingestion")
    ingest.add_argument("--update", action="store_true")
    ingest.add_argument("--force", action="store_true")
    ingest.add_argument("--parser", choices=("auto", "mineru", "pymupdf"),
                        help="Prefer local MinerU if installed; failures never silently fall back")
    ingest.add_argument("--retries", type=int, default=1)
    ingest.add_argument("--model", help="Override PAPER_CODEX_MODEL (default: gpt-6.1-sol)")
    ingest.add_argument("--reasoning-effort", choices=REASONING_EFFORTS,
                        help="Override PAPER_CODEX_REASONING_EFFORT (default: high)")
    sub.add_parser("rebuild-index")
    options = parser.parse_args(args)
    try:
        if options.command == "rebuild-index":
            index = PaperService(progress=console_progress).rebuild_index()
            print(f"Rebuilt index: {len(index['papers'])} papers")
            return 0
        if options.retries < 0 or options.retries > 3:
            parser.error("--retries must be 0..3")
        metadata = json.loads(options.metadata.read_text()) if options.metadata else {}
        source = options.pdf
        if options.zotero_item:
            console_progress("读取 Zotero 元数据与本地附件（只读）。")
            from tools.papers.zotero import ZoteroAdapter
            source, zotero_metadata = ZoteroAdapter().resolve(options.zotero_item, pdf_override=source)
            metadata.update(zotero_metadata)
        if source is None:
            parser.error("Provide a local PDF or --zotero-item")
        service = PaperService(analyzer=CodexAnalyzer(model=options.model, reasoning_effort=options.reasoning_effort,
                                                     progress=console_progress), progress=console_progress,
                               parser_name=options.parser)
        paper = service.ingest(source, metadata=metadata, update=options.update,
                               force=options.force, paper_id=options.id, retries=options.retries)
        print(f"Published: {paper['id']} → data/papers/{paper['id']}.json")
        return 0
    except KeyboardInterrupt:
        console_progress("导入已取消，独立分析子进程已停止；没有发布半成品。")
        return 130
    except Exception as error:
        print(f"Paper ingest failed: {error}\nExisting library preserved. Logs: PAPER_CACHE_DIR/<source-hash>/last-error.log", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
