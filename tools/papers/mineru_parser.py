"""Local MinerU 4 CLI adapter; stable page/block locators, including vector figures."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from .parsers import PaperParser
from .progress import quiet_progress

PAGE = re.compile(r"<!--\s*page (\d+) of (\d+)\s*-->")
IMAGE = re.compile(r"!\[[^\]]*\]\((doc:[a-f0-9]+/tier:[a-z]+/page:(\d+)/block:(\d+))\)")


def mineru_binary() -> str | None:
    configured = os.environ.get("PAPER_MINERU_BIN")
    if configured:
        return configured
    installed = shutil.which("mineru")
    local = Path.home() / ".local/bin/mineru"
    return installed or (str(local) if local.is_file() else None)


class MinerUParser(PaperParser):
    def __init__(self, *, cache_dir: Path, binary: str | None = None, progress=None):
        self.binary = binary or mineru_binary()
        if not self.binary:
            raise ValueError("MinerU CLI unavailable; install MinerU 4 or explicitly use --parser pymupdf")
        self.work = cache_dir / "mineru-cli"
        self.report = progress or quiet_progress
        self.tier = os.environ.get("PAPER_MINERU_TIER", "")
        if self.tier not in {"", "basic", "standard", "advanced"}:
            raise ValueError("PAPER_MINERU_TIER must be basic, standard or advanced; no automatic flash/remote fallback")
        self.version = "mineru-cli-figures-1.2-" + (self.tier or "default")
        self.timeout = int(os.environ.get("PAPER_MINERU_TIMEOUT", "900"))
        if self.timeout <= 0:
            raise ValueError("PAPER_MINERU_TIMEOUT must be positive")
        self._checked = False

    def _run(self, args: list[str]) -> dict:
        self.work.mkdir(parents=True, exist_ok=True)
        # Keep even unexpected CLI-local artifacts inside ignored cache, not cwd.
        process = subprocess.Popen([self.binary, *args, "--json"], cwd=self.work,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        started = time.monotonic()
        try:
            while True:
                remaining = self.timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("MinerU CLI timed out; inspect mineru show/list parses; no fallback or upload performed")
                try:
                    output, stderr = process.communicate(timeout=min(15, remaining))
                    break
                except subprocess.TimeoutExpired:
                    self.report(f"等待 MinerU 本地解析/读取：{time.monotonic() - started:.0f}s（未上传论文）。")
        except BaseException:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            # MinerU's separately managed server is not ours to terminate.
            raise
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"MinerU returned invalid JSON (exit {process.returncode}); stderr: {stderr[-500:]}") from error
        if "error" in payload:
            error = payload["error"]
            raise RuntimeError(f"MinerU {error.get('code')}: {error.get('message')}; "
                               "local-only: choose/configure an available quality tier or explicitly authorize another parser")
        if process.returncode:
            raise RuntimeError(f"MinerU exited {process.returncode}; no automatic fallback")
        return payload

    def parse(self, source: Path) -> dict:
        if not self._checked:
            installed = self._run(["version"])["mineru_version"]
            if not re.fullmatch(r"4\.\d+\.\d+(?:[-+].*)?", installed):
                raise ValueError(f"MinerU 4 required, found {installed}; no automatic upgrade")
            self._checked = True
        self.report("使用 MinerU 本地解析正文与完整图块（包括矢量图）；不启用 remote。")
        args = ["parse", str(source), "--pages", "all", "--limit", "60000", "--wait", "120"]
        if self.tier:
            args.extend(["--tier", self.tier])
        payload = self._run(args)
        chunks, visited = [], set()
        tier = payload.get("parse", {}).get("tier")
        if tier not in {"basic", "standard", "advanced"}:
            raise ValueError(f"MinerU normal-quality local parsing required, received {tier!r}")
        for _ in range(100):
            content = payload.get("content")
            if not isinstance(content, dict) or not isinstance(content.get("content"), str):
                raise RuntimeError("MinerU parse pending/incomplete; inspect mineru show file/list parses and retry later")
            if content.get("tier") != tier:
                raise ValueError("MinerU changed quality tier during continuation")
            chunks.append(content["content"])
            if not content.get("truncated"):
                break
            next_request = content.get("next_request") or {}
            fingerprint = json.dumps(next_request, sort_keys=True)
            if not next_request or fingerprint in visited:
                raise ValueError("MinerU continuation missing/repeated; refusing truncated analysis")
            visited.add(fingerprint)
            if next_request.get("locator"):
                payload = {"content": self._run(["read", next_request["locator"], "--limit", "60000"])}
            elif next_request.get("page_range"):
                # Follow the exact requested range; it is already cached locally.
                continuation = ["parse", str(source), "--pages", next_request["page_range"],
                                "--tier", tier, "--limit", "60000", "--wait", "120"]
                if next_request.get("after"):
                    continuation.extend(["--after", next_request["after"]])
                payload = self._run(continuation)
            else:
                raise ValueError("Unsupported MinerU continuation; refusing truncated analysis")
        else:
            raise ValueError("MinerU continuation limit exceeded")
        return self.from_markdown("\n\n".join(chunks), tier, payload.get("content", {}).get("short_id"))

    def from_markdown(self, text: str, tier: str, doc_id: str | None = None) -> dict:
        matches = list(PAGE.finditer(text))
        if not matches:
            raise ValueError("MinerU returned no stable page markers; cannot ground evidence")
        total = int(matches[0].group(2))
        page_text = {}
        for index, marker in enumerate(matches):
            page = int(marker.group(1))
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            page_text.setdefault(page, []).append(text[marker.end():end].strip())
        if set(page_text) != set(range(1, total + 1)):
            raise ValueError("MinerU missing paper pages; refusing partial full-paper analysis")
        pages, figures, references = [], [], []
        section = ""
        for page, chunks in sorted(page_text.items()):
            body = "\n\n".join(chunks)
            paragraphs = []
            for block in re.split(r"\n\s*\n", body):
                if not block.strip():
                    continue
                heading = re.match(r"^#{1,6}\s+(.+)", block)
                if heading:
                    section = heading.group(1)
                image = IMAGE.search(block)
                kind = "caption" if re.match(r"^(?:fig(?:ure)?\.?|table)\s*\d+", block, re.I) else "paragraph"
                if image and (int(image.group(2)) != page or
                              (doc_id and not image.group(1).startswith(f"doc:{doc_id}/tier:{tier}/"))):
                    raise ValueError("Figure locator belongs to another document/tier/page")
                paragraph = {"text": block.strip(), "section": section, "kind": kind}
                if image:
                    paragraph["locator"] = image.group(1)
                paragraphs.append(paragraph)
                if re.search(r"\b(references|bibliography)\b", section, re.I):
                    references.append({"page": page, "text": block.strip()})
                if image:
                    after = body[body.find(image.group(0)) + len(image.group(0)):]
                    caption = ""
                    for following in re.split(r"\n\s*\n", after):
                        following = following.strip()
                        if not following or IMAGE.fullmatch(following):
                            continue
                        if re.match(r"^Fig(?:ure)?\.?\s*\d+", following, re.I):
                            caption = following
                        break  # Never steal a caption across intervening body text/headings.
                    figures.append({"id": f"F{len(figures) + 1}", "page": page,
                                    "locator": image.group(1), "section": section,
                                    "caption": caption})
            pages.append({"page": page, "paragraphs": paragraphs})
        # MinerU can split a multi-panel figure into several image blocks. Count
        # it as ONE logical figure, keeping every panel and its exact locator.
        grouped, groups = [], {}
        for figure in figures:
            key = (figure["page"], figure["caption"] or figure["locator"])
            if key in groups:
                first = groups[key]
                first.setdefault("panel_locators", [first["locator"]]).append(figure["locator"])
                first["layout_note"] = "Panels are stacked for inspection; original relative layout is not preserved."
            else:
                figure["id"] = f"F{len(grouped) + 1}"
                groups[key] = figure
                grouped.append(figure)
        return {"parser_version": self.version, "page_count": total, "pages": pages,
                "metadata_hint": {}, "references": references, "figure_candidates": grouped,
                "warnings": ["MinerU local OCR/layout text; confirm critical claims against the original."]}

    def extract_figure(self, source: Path, candidate: dict) -> bytes:
        from PIL import Image
        locators = candidate.get("panel_locators", [candidate.get("locator", "")])
        for locator in locators:
            if not re.fullmatch(r"doc:[a-f0-9]+/tier:(?:basic|standard|advanced)/page:\d+/block:\d+", locator):
                raise ValueError("Invalid MinerU figure block locator")
        if len(locators) > 12:
            raise ValueError("Too many blocks in one figure; inspect its MinerU layout before publishing")
        self.work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="figure-", dir=self.work) as directory:
            panels = []
            for index, locator in enumerate(locators):
                output = Path(directory) / f"panel-{index}.png"
                self._run(["read", locator, "--format", "image", "--output", str(output)])
                with Image.open(output) as original:
                    panel = original.convert("RGB")
                    panel.thumbnail((2200, 2200))
                    panels.append(panel)
            image = panels[0]
            if len(panels) > 1:
                gap = 24
                image = Image.new("RGB", (max(p.width for p in panels),
                    sum(p.height for p in panels) + gap * (len(panels) - 1)), "white")
                y = 0
                for panel in panels:
                    image.paste(panel, ((image.width - panel.width) // 2, y))
                    y += panel.height + gap
                image.thumbnail((2200, 4400))
            result = io.BytesIO()
            image.save(result, format="WEBP", quality=88, method=6)
            return result.getvalue()
