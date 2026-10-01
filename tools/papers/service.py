"""Framework-independent ingestion and query service; CLI is only a wrapper."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import traceback
import uuid

from .analyzers.base import PaperAnalyzer
from .parsers import PaperParser, overview_candidates, preferred_parser
from .progress import ProgressCallback, quiet_progress
from .storage import (ROOT, SCHEMA_PATH, PROMPT_VERSION, SCHEMA_VERSION, PaperStore,
                      atomic_write, build_index, choose_id, encode, identity, normalize_title, validate)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_grounding(paper: dict, parsed: dict, metadata: dict):
    pages = {page["page"]: normalized_text("\n".join(p["text"] for p in page["paragraphs"]))
             for page in parsed["pages"]}
    for evidence in paper["evidence"]:
        excerpt = normalized_text(evidence["content"])
        if evidence["page"] not in pages or excerpt not in pages[evidence["page"]]:
            raise ValueError(f"Evidence {evidence['id']} is not a contiguous excerpt of its stated PDF page")
        if len(excerpt.split()) > 25:
            raise ValueError("Evidence excerpt exceeds 25-word limit")
    for section in ("overview", "method", "experiments"):
        values = paper[section]
        if any(value for key, value in values.items() if key != "evidence_ids") and not values["evidence_ids"]:
            raise ValueError(f"Populated {section} requires supporting evidence")
    corpus = " ".join(pages.values())
    for relation in paper["related_papers"]:
        if relation["source"] == "external_metadata":
            candidates = [p for p in metadata.get("related_papers", [])
                          if normalize_title(p.get("title", "")) == normalize_title(relation["title"])]
            if len(candidates) != 1:
                raise ValueError("Related paper is not in the supplied trusted external metadata")
            supplied = candidates[0]
            if any(relation[key] and relation[key] != supplied.get(key) for key in ("doi", "arxiv_id", "url")):
                raise ValueError("External related paper identifier differs from supplied metadata")
            if relation["relation_type"] != supplied.get("relation_type", "related"):
                raise ValueError("External metadata does not support this relationship")
            continue
        for key in ("doi", "arxiv_id", "url"):
            if relation[key] and relation[key] not in corpus:
                raise ValueError("Related-paper identifiers/URLs must occur in source text, not model memory")
    for key in ("doi", "arxiv_id", "paper_url", "pdf_url", "project_url", "code_url"):
        value = paper["metadata"][key]
        if value and not metadata.get(key) and value not in corpus:
            raise ValueError(f"Extracted metadata {key} is not present in the supplied paper")


def validate_figure_selection(paper: dict, parsed: dict):
    allowed = {candidate["id"]: candidate for candidate in overview_candidates(parsed)}
    if len(paper["figures"]) > 3:
        raise ValueError("Select at most three key concept/architecture/method figures")
    for figure in paper["figures"]:
        candidate = allowed.get(figure["id"])
        if not candidate or figure["page"] != candidate["page"]:
            raise ValueError("Figure must be a captioned early-paper overview candidate")
        if figure["type"] not in {"concept_overview", "architecture", "method_overview"}:
            raise ValueError("Experiment/result/benchmark figures are not selected for publication")


def match_related(paper: dict, records: list[dict]):
    for related in paper["related_papers"]:
        matches = []
        for record in records:
            meta = record["metadata"]
            same_doi = related["doi"] and meta["doi"] and identity({"doi": related["doi"]}) == identity(meta)
            same_arxiv = related["arxiv_id"] and meta["arxiv_id"] and re.sub(r"v\d+$", "", related["arxiv_id"]) == re.sub(r"v\d+$", "", meta["arxiv_id"])
            same_title = normalize_title(related["title"]) == normalize_title(meta["title"])
            if same_doi or same_arxiv or same_title:
                matches.append(record["id"])
        related["paper_id"] = matches[0] if len(matches) == 1 and matches[0] != paper["id"] else None


class PaperService:
    def __init__(self, analyzer: PaperAnalyzer | None = None, parser: PaperParser | None = None,
                 root: Path = ROOT, cache_dir: Path | None = None,
                 progress: ProgressCallback | None = None, parser_name: str | None = None):
        self.store = PaperStore(root)
        self.analyzer = analyzer
        self.report = progress or quiet_progress
        configured = cache_dir or Path(os.environ.get("PAPER_CACHE_DIR", ".cache/papers")).expanduser()
        self.cache = (configured if configured.is_absolute() else root / configured).resolve()
        # Custom caches inside the repository MUST stay under its ignored .cache directory.
        if self.cache.is_relative_to(root.resolve()) and not self.cache.is_relative_to((root / ".cache").resolve()):
            raise ValueError("In-repository PAPER_CACHE_DIR must be inside .cache/ to avoid publication")
        self.parser = parser or preferred_parser(self.cache, progress=self.report, name=parser_name)

    def list_papers(self) -> list[dict]:
        with self.store.lock():
            return build_index(self.store.records(), self.store.manual_tags())["papers"]

    def get_paper(self, paper_id: str) -> dict | None:
        with self.store.lock():
            paper = next((p for p in self.store.records() if p["id"] == paper_id), None)
            if paper:
                from .tags import tag_names
                paper["tags"] = tag_names(self.store.manual_tags(), paper_id)
            return paper

    def search_papers(self, query: str = "", tag: str | None = None, year: int | None = None) -> list[dict]:
        query = query.casefold()
        return [p for p in self.list_papers() if
                query in " ".join([p["title"], p["short_title"], *p["authors"], *p["tags"]]).casefold()
                and (not tag or tag in p["tags"]) and (year is None or p["year"] == year)]

    def related_papers(self, paper_id: str) -> list[dict]:
        paper = self.get_paper(paper_id)
        if not paper:
            return []
        with self.store.lock():
            match_related(paper, self.store.records())
        return paper["related_papers"]

    def rebuild_index(self) -> dict:
        self.report("校验单篇论文，重建轻量索引。")
        return self.store.rebuild_index()

    def ingest(self, source: Path, *, metadata: dict | None = None, update: bool = False,
               force: bool = False, paper_id: str | None = None, retries: int = 1) -> dict:
        if self.analyzer is None:
            raise ValueError("Ingestion requires a PaperAnalyzer")
        self.report("[1/6] 读取本地 PDF，计算 source hash。")
        source = source.expanduser().resolve(strict=True)
        source_hash = file_hash(source)
        source_cache = self.cache / source_hash
        source_cache.mkdir(parents=True, exist_ok=True)
        self.report(f"论文：{source.name} · Cache：{source_cache}")
        atomic_write(source_cache / "local-source.json", encode({"path": str(source), "source_hash": source_hash}))
        try:
            supplied = copy.deepcopy(metadata or {})
            allowed = {"title", "short_title", "authors", "year", "venue", "doi", "arxiv_id", "paper_url",
                       "pdf_url", "project_url", "code_url", "zotero", "tags", "related_papers"}
            if not isinstance(supplied, dict) or set(supplied) - allowed:
                raise ValueError("Metadata must be an object containing only documented bibliographic fields")
            # Website taxonomy is curated separately; neither LLM nor Zotero supplies it.
            supplied.pop("tags", None)
            with self.store.lock():
                existing = self.store.records()
            old = next((p for p in existing if p["analysis_meta"]["source_hash"] == source_hash), None)
            if old and not update:
                raise ValueError(f"Paper {old['id']} already exists; pass --update")
            parser_key = hashlib.sha256(self.parser.version.encode()).hexdigest()[:12]
            self.report("[2/6] 解析 PDF：页面、文本、图注与参考文献。")
            parse_path = source_cache / f"parsed-{parser_key}.json"
            if parse_path.exists() and not force:
                parsed = json.loads(parse_path.read_text())
                self.report("命中 PDF 解析缓存。")
            else:
                parsed = self.parser.parse(source)
                if file_hash(source) != source_hash:
                    raise ValueError("Source PDF changed while parsing; retry after saving the file")
                atomic_write(parse_path, encode(parsed))
            if file_hash(source) != source_hash:
                raise ValueError("Source PDF changed during ingest; retry after saving the file")
            size = len(encode(parsed))
            self.report(f"解析完成：{len(parsed['pages'])} 页 · {size:,} bytes · {len(parsed['figure_candidates'])} 个图片候选。")
            if size > int(os.environ.get("PAPER_MAX_PARSED_BYTES", "1500000")):
                raise ValueError("Parsed paper exceeds input limit; not silently truncated. Raise PAPER_MAX_PARSED_BYTES if appropriate")
            version_data = {"source_hash": source_hash, "schema_version": SCHEMA_VERSION,
                            "prompt_version": PROMPT_VERSION, "parser_version": self.parser.version,
                            "schema_hash": file_hash(SCHEMA_PATH),
                            "prompt_hash": file_hash(ROOT / "tools/papers/prompts/analyze.md"),
                            "analyzer": self.analyzer.cache_identity(), "metadata": supplied}
            cache_key = hashlib.sha256(encode(version_data)).hexdigest()
            result_path = source_cache / f"analysis-{cache_key}.json"
            self.report("[3/6] 获取结构化论文分析。")
            if result_path.exists() and not force:
                paper = json.loads(result_path.read_text())
                self.report("命中相同模型/推理配置的分析缓存，不启动 Codex。")
                self.report("[4/6] 校验缓存 JSON Schema 与原文证据。")
                validate(paper, published=False)
                validate_grounding(paper, parsed, supplied)
                validate_figure_selection(paper, parsed)
            else:
                visual_parsed = copy.deepcopy(parsed)
                visual_parsed["figure_candidates"] = overview_candidates(parsed)
                self.report(f"准备 {len(visual_parsed['figure_candidates'])} 张前半部分图块供实际视觉检查；最终仅选 1–3 张。")
                # In-memory image bytes, never paths/base64 in the prompt or published JSON.
                visual_parsed["_figure_images"] = {}
                for candidate in visual_parsed["figure_candidates"]:
                    image = self.parser.extract_figure(source, candidate)
                    if not image:
                        raise ValueError("Candidate figure extraction produced no visual input")
                    visual_parsed["_figure_images"][candidate["id"]] = image
                analysis_meta = {"analyzer": self.analyzer.name,
                                 "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
                                 "source_hash": source_hash, "parser_version": self.parser.version}
                for attempt in range(retries + 1):
                    task = source_cache / "tasks" / str(uuid.uuid4())
                    task.parent.mkdir(exist_ok=True)
                    try:
                        self.report(f"分析尝试 {attempt + 1}/{retries + 1}，使用全新独立任务。")
                        paper = self.analyzer.analyze(visual_parsed, {**supplied, "analysis_meta": analysis_meta}, task)
                        self.report("[4/6] 校验 JSON Schema、证据引用和页内原文。")
                        # Never trust generated provenance or links to other library records.
                        paper["analysis_meta"] = analysis_meta
                        paper["id"] = "draft"
                        for key in ("title", "short_title", "authors", "year", "venue", "doi", "arxiv_id",
                                    "paper_url", "pdf_url", "project_url", "code_url", "zotero"):
                            if supplied.get(key):
                                paper["metadata"][key] = copy.deepcopy(supplied[key])
                        paper["tags"] = []
                        for related in paper["related_papers"]:
                            related["paper_id"] = None
                        validate(paper, published=False)
                        validate_grounding(paper, parsed, supplied)
                        validate_figure_selection(paper, parsed)
                        atomic_write(result_path, encode(paper))
                        break
                    except Exception as error:
                        if attempt >= retries:
                            raise
                        self.report(f"本次尝试失败：{type(error).__name__}: {error}")
                        self.report("准备重试：新进程、新目录，不沿用上一轮上下文。")
                        # A retry is ANOTHER fresh process, never repair via resume/context reuse.
            paper["tags"] = []  # Also enforce for cached analyzer output.
            with self.store.lock():
                records = self.store.records()
                paper["id"] = choose_id(paper["metadata"], records, source_hash, paper_id)
                if any(p["id"] == paper["id"] for p in records) and not update:
                    raise ValueError("Paper already exists; pass --update")
                match_related(paper, records)
            candidates = {f["id"]: f for f in parsed["figure_candidates"]}
            validate_figure_selection(paper, parsed)
            images = {}
            self.report(f"[5/6] 处理 {len(paper['figures'])} 张选中的展示图。")
            for figure in paper["figures"]:
                candidate = candidates.get(figure["id"])
                if not candidate or figure["page"] != candidate["page"]:
                    raise ValueError("Figure selection is not a parsed candidate")
                figure["caption"] = candidate["caption"]
                image = self.parser.extract_figure(source, candidate)
                if not image:
                    raise ValueError("Figure extraction produced an empty asset")
                filename = candidate["id"].lower() + ".webp"
                atomic_write(source_cache / "figures" / filename, image)
                figure["path"] = f"/paper-assets/{paper['id']}/{filename}"
                images[figure["path"]] = image
            if file_hash(source) != source_hash:
                raise ValueError("Source PDF changed while analyzing; publication cancelled")
            self.report(f"[6/6] 发布 {paper['id']}.json，并更新轻量索引（可恢复事务）。")
            self.store.publish(paper, images, update=update)
            self.report("完成：正式论文 JSON、索引和选中资产已发布。")
            return paper
        except KeyboardInterrupt:
            atomic_write(source_cache / "last-error.log", b"Ingest interrupted by user; publication not completed.\n")
            self.report(f"用户中断导入；不会发布半成品。日志：{source_cache / 'last-error.log'}")
            raise
        except Exception as error:
            # Normal operation retains a short error, not a raw task transcript.
            message = (traceback.format_exc() if getattr(self.analyzer, "keep_logs", False) else
                       f"{type(error).__name__}: {str(error)[:1000]}\n")
            atomic_write(source_cache / "last-error.log", message.encode())
            self.report(f"导入失败，正式库不会写入半成品；错误日志：{source_cache / 'last-error.log'}")
            raise
