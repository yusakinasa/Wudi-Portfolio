#!/usr/bin/env python3
"""Sequential ingest, verified publication, then no-overwrite original PDF archival."""
from __future__ import annotations

import argparse
from datetime import datetime
import errno
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import uuid

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.papers.service import file_hash
from tools.papers.storage import ROOT, PaperStore, atomic_write, build_index, encode


def published_paper(store: PaperStore, source_hash: str) -> dict | None:
    """Do not trust a CLI exit code alone; check schema, assets and committed index."""
    with store.lock():
        records = store.records()
        matches = [p for p in records if p["analysis_meta"]["source_hash"] == source_hash]
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("同一 PDF 对应多个记录，拒绝移动原文；请先解决重复记录")
        index = store.data / "index.json"
        if not index.is_file() or json.loads(index.read_text()) != build_index(records, store.manual_tags()):
            raise ValueError("论文索引不完整或不同步，拒绝移动原文；请先 rebuild-index")
        return matches[0]


def file_state(path: Path) -> tuple:
    if path.is_symlink() or not path.is_file():
        raise ValueError("只处理普通 PDF 文件，不处理符号链接")
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def archive_pdf(source: Path, ready: Path, source_hash: str) -> Path:
    """Exclusive destination creation, verify exact bytes, only then unlink source."""
    before = file_state(source)
    if file_hash(source) != source_hash or file_state(source) != before:
        raise ValueError("PDF 在分析后发生变化，原文保留在 new")
    ready.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        suffix = "" if not attempt else f"--{source_hash[:8]}" + (f"-{attempt}" if attempt > 1 else "")
        destination = ready / f"{source.stem}{suffix}{source.suffix}"
        try:
            # Same-volume hard link + unlink preserves the original inode/metadata;
            # unlike rename()/shutil.move(), link() cannot overwrite a destination.
            os.link(source, destination, follow_symlinks=False)
        except FileExistsError:
            attempt += 1
            continue
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            try:
                target = destination.open("xb")
            except FileExistsError:
                attempt += 1
                continue
            try:
                with target, source.open("rb") as original:
                    shutil.copyfileobj(original, target)
                    target.flush()
                    os.fsync(target.fileno())
                shutil.copystat(source, destination, follow_symlinks=False)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        try:
            if (file_state(source) != before or file_hash(source) != source_hash
                    or file_hash(destination) != source_hash):
                raise ValueError("归档验证失败，原文保留在 new")
            source.unlink()
        except BaseException:
            # SIGINT can arrive immediately after unlink() succeeded. Never
            # delete the archived original if the source is already gone.
            if source.exists():
                destination.unlink(missing_ok=True)
            raise
        return destination


def run_ingest(source: Path, root: Path, model: str, effort: str) -> int:
    """One fresh ingest subprocess per PDF, with live, inherited terminal output."""
    command = [sys.executable, "-u", str(root / "tools/papers/ingest.py"),
               str(source), "--model", model, "--reasoning-effort", effort]
    process = subprocess.Popen(command, cwd=root, start_new_session=True)
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Isolate the ingest process from terminal broadcasts so it receives
        # exactly one cancellation while cleaning up its own Codex process group.
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        while True:
            try:
                process.wait()
                break
            except KeyboardInterrupt:
                print("正在等待当前独立分析进程安全退出…", flush=True)
        raise


def run_batch(incoming: Path, ready: Path, *, root: Path = ROOT,
              model: str = "gpt-6.1-sol", effort: str = "high", runner=run_ingest) -> int:
    root = root.resolve()
    incoming, ready = incoming.expanduser().resolve(), ready.expanduser().resolve()
    if not incoming.is_dir():
        raise ValueError(f"待处理目录不存在：{incoming}")
    if incoming == ready:
        raise ValueError("new 与 ready 不能是同一个目录")
    cache = root / ".cache/papers/batches"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / ".batch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("已有批量导入正在运行；请不要重复双击") from None
        store = PaperStore(root)
        sources = sorted((p for p in incoming.iterdir() if p.suffix.lower() == ".pdf"
                          and p.is_file() and not p.is_symlink()), key=lambda p: (p.name.casefold(), p.name))
        report = cache / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".json")
        results = []
        print(f"待处理：{len(sources)} 篇（仅目录第一层普通 PDF）\n"
              f"JSON 输出固定为：{store.data}\n成功后移至：{ready}\n"
              f"模型：{model} / {effort}\n批次记录：{report}", flush=True)
        atomic_write(report, encode({"status": "running", "results": results}))
        interrupted = False
        for number, source in enumerate(sources, 1):
            result = {"source": str(source), "status": "failed"}
            print(f"\n[{number}/{len(sources)}] {source.name}", flush=True)
            try:
                state = file_state(source)
                digest = file_hash(source)
                if file_state(source) != state:
                    raise ValueError("读取期间 PDF 发生变化")
                paper = published_paper(store, digest)
                if paper is None:
                    status = runner(source, root, model, effort)
                    if status in {130, -signal.SIGINT}:
                        raise KeyboardInterrupt
                    if status != 0:
                        raise RuntimeError(f"ingest 退出码 {status}，PDF 保留在 new")
                    paper = published_paper(store, digest)
                    if paper is None:
                        raise ValueError("未找到匹配 PDF hash 的正式 JSON，拒绝移动原文")
                else:
                    print(f"已存在完整入库记录 {paper['id']}，跳过重复分析，仅归档原文。", flush=True)
                if file_state(source) != state:
                    raise ValueError("分析期间原始 PDF 被替换或修改，拒绝移动")
                destination = archive_pdf(source, ready, digest)
                result.update(status="archived", paper_id=paper["id"], destination=str(destination))
                print(f"成功：{store.data / (paper['id'] + '.json')}\n原文已移至：{destination}", flush=True)
            except KeyboardInterrupt:
                interrupted = True
                result.update(status="cancelled", error="用户中断；停止后续论文")
                print("\n已停止。已归档原文保留在 ready，其余保留在 new，可再次双击继续。", flush=True)
            except Exception as error:
                result["error"] = str(error)
                print(f"失败：{error}\n继续处理下一篇。", flush=True)
            results.append(result)
            atomic_write(report, encode({"status": "cancelled" if interrupted else "running", "results": results}))
            if interrupted:
                break
        atomic_write(report, encode({"status": "cancelled" if interrupted else "finished", "results": results}))
        successes = sum(r["status"] == "archived" for r in results)
        failures = sum(r["status"] == "failed" for r in results)
        print(f"\n批次结束：成功归档 {successes} 篇，失败 {failures} 篇，"
              f"未尝试 {len(sources) - len(results)} 篇。\n记录：{report}", flush=True)
        return 130 if interrupted else (1 if failures else 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="逐篇导入 PDF；验证正式 JSON/索引后移动原文，不覆盖现有文件")
    parser.add_argument("--new", type=Path, default=Path.home() / "paper_reading/new")
    parser.add_argument("--ready", type=Path, default=Path.home() / "paper_reading/ready")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"), default="high")
    args = parser.parse_args(argv)
    try:
        return run_batch(args.new, args.ready, model=args.model, effort=args.reasoning_effort)
    except KeyboardInterrupt:
        print("\n批次已取消。", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"批次未完成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
