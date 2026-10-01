"""Replaceable, page-aware PDF parser. Original PDFs are opened in place."""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
import re
import math
import os


def preferred_parser(cache_dir: Path, progress=None, name: str | None = None) -> PaperParser:
    from .mineru_parser import MinerUParser, mineru_binary
    name = name or os.environ.get("PAPER_PARSER", "auto")
    if name not in {"auto", "mineru", "pymupdf"}:
        raise ValueError("PAPER_PARSER must be auto, mineru or pymupdf")
    if name == "mineru" or (name == "auto" and mineru_binary()):
        return MinerUParser(cache_dir=cache_dir, progress=progress)
    if progress:
        progress("使用 PyMuPDF（显式指定或 MinerU CLI 未安装）；位图候选可能遗漏矢量架构图。")
    return PyMuPDFParser()


def overview_candidates(parsed: dict) -> list[dict]:
    """Bound visual inputs to early, captioned diagrams; the AI makes the final choice."""
    halfway = math.ceil(parsed.get("page_count", len(parsed["pages"])) / 2)
    candidates = []
    for figure in parsed["figure_candidates"]:
        caption = figure.get("caption", "")
        if not caption or figure["page"] > halfway:
            continue
        is_overview = bool(re.search(r"\b(overview|architecture|pipeline|framework|workflow|concept)\b", caption, re.I))
        excluded_section = re.search(r"\b(experiments?|results?|evaluation|ablation|appendix|references|bibliography)\b",
                                     figure.get("section", ""), re.I)
        if excluded_section and not is_overview:
            continue
        candidates.append(figure)
    # Keep early overview candidates first, but do not equate captions with visual proof.
    return sorted(candidates, key=lambda f: (not bool(re.search(
        r"\b(overview|architecture|framework|pipeline)\b", f["caption"], re.I)), f["page"], f["id"]))[:8]


class PaperParser(ABC):
    version: str

    @abstractmethod
    def parse(self, source: Path) -> dict: ...

    @abstractmethod
    def extract_figure(self, source: Path, candidate: dict) -> bytes: ...


class PyMuPDFParser(PaperParser):
    version = "pymupdf-blocks-1.1"

    def parse(self, source: Path) -> dict:
        import pymupdf
        pages, candidates, references = [], [], []
        section, in_references = "", False
        with pymupdf.open(source) as doc:
            if doc.needs_pass:
                raise ValueError("Encrypted PDF: decrypt locally before ingestion")
            pdf_meta = {key: value for key, value in doc.metadata.items()
                        if key in {"title", "author"} and value}
            for number, page in enumerate(doc, 1):
                blocks = [b for b in page.get_text("blocks", sort=True) if b[6] == 0 and b[4].strip()]
                midpoint = page.rect.width / 2
                # Two-column heuristic, preserving full-width headings as band boundaries.
                left = [b for b in blocks if b[2] < midpoint + 18 and b[0] < midpoint - 30]
                right = [b for b in blocks if b[0] > midpoint - 18]
                if len(left) >= 3 and len(right) >= 3:
                    spans = sorted([b for b in blocks if b not in left and b not in right], key=lambda b: b[1])
                    ordered, last_y = [], -1
                    for span in spans:
                        ordered.extend(sorted([b for b in left if last_y <= b[1] < span[1]], key=lambda b: b[1]))
                        ordered.extend(sorted([b for b in right if last_y <= b[1] < span[1]], key=lambda b: b[1]))
                        ordered.append(span)
                        last_y = span[1]
                    ordered.extend(sorted([b for b in left if b[1] >= last_y], key=lambda b: b[1]))
                    ordered.extend(sorted([b for b in right if b[1] >= last_y], key=lambda b: b[1]))
                    blocks = ordered
                paragraphs = []
                for block in blocks:
                    text = block[4].strip()
                    if re.match(r"^(?:\d+(?:\.\d+)*\s+)?(?:abstract|introduction|related work|method\w*|experiment\w*|results|discussion|limitations|conclusion|references|bibliography)\b", text, re.I) and len(text) < 110:
                        section = text
                        in_references = bool(re.match(r"^(references|bibliography)\b", text, re.I))
                    caption = bool(re.match(r"^(?:fig(?:ure)?\.?|table)\s*\d+", text, re.I))
                    paragraphs.append({"section": section, "text": text, "bbox": list(block[:4]),
                                       "kind": "caption" if caption else "reference" if in_references else "paragraph"})
                    if in_references:
                        references.append({"page": number, "text": text})
                # Only actual raster image regions are candidates in v1. Avoid full-page screenshots.
                for image in page.get_image_info():
                    bbox = list(image["bbox"])
                    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    if width < 80 or height < 55 or width * height > page.rect.width * page.rect.height * .75:
                        continue
                    captions = [p for p in paragraphs if p["kind"] == "caption" and p["bbox"][1] >= bbox[3] - 20]
                    nearest = min(captions, key=lambda p: p["bbox"][1] - bbox[3], default=None)
                    if nearest and nearest["bbox"][1] - bbox[3] > 100:
                        nearest = None
                    candidates.append({"id": f"F{len(candidates) + 1}", "page": number, "bbox": bbox,
                                       "section": nearest["section"] if nearest else section,
                                       "caption": nearest["text"] if nearest else ""})
                pages.append({"page": number, "paragraphs": paragraphs})
        count = sum(len(p["text"]) for page in pages for p in page["paragraphs"])
        if count < 80:
            raise ValueError("PDF has insufficient text (possibly scanned); OCR/parser replacement required")
        return {"parser_version": self.version, "metadata_hint": pdf_meta, "page_count": len(pages),
                "pages": pages, "references": references, "figure_candidates": candidates,
                "warnings": ["Heuristic column order; verify evidence against the PDF.",
                             "Only raster figures are candidates; vector diagrams need a future parser."]}

    def extract_figure(self, source: Path, candidate: dict) -> bytes:
        import io
        import pymupdf
        from PIL import Image
        with pymupdf.open(source) as doc:
            page = doc[candidate["page"] - 1]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6),
                                    clip=pymupdf.Rect(candidate["bbox"]), alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            image.thumbnail((1600, 1600))
            result = io.BytesIO()
            image.save(result, format="WEBP", quality=82, method=6)
            return result.getvalue()
