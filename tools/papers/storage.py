"""Validation, stable identities, lightweight indexing and recoverable publication."""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import urlsplit

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas/paper.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())
VALIDATOR = Draft7Validator(SCHEMA)
SCHEMA_VERSION = "1.0"
PROMPT_VERSION = "1.2"
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".paper-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def safe_url(value: str) -> bool:
    if not value:
        return True
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"https", "http"} and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                and not re.search(r"[\s\\\x00-\x1f]", value))
    except ValueError:
        return False


def validate(paper: dict, *, published: bool = True):
    errors = sorted(VALIDATOR.iter_errors(paper), key=lambda e: str(e.path))
    if errors:
        raise ValueError("Schema validation: " + "; ".join(
            f"{'.'.join(map(str, e.path))}: {e.message}" for e in errors[:8]))
    if paper["id"] in {"index", "tags"}:
        raise ValueError("Paper ID is reserved for an index/management page")
    all_ids = [e["id"] for e in paper["evidence"]]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("Duplicate evidence IDs")
    evidence = set(all_ids)

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence_ids" and not set(item).issubset(evidence):
                    raise ValueError("Unknown evidence reference")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and re.search(r"(?:/Users/|/home/|file://|[A-Za-z]:\\Users\\)", value):
            raise ValueError("Private local path in publication data")
    walk(paper)
    for key in ("paper_url", "pdf_url", "project_url", "code_url"):
        if not safe_url(paper["metadata"][key]):
            raise ValueError(f"Unsafe metadata URL: {key}")
    for relation in paper["related_papers"]:
        if not safe_url(relation["url"]):
            raise ValueError("Unsafe related paper URL")
        if relation["source"] != "external_metadata" and not relation["evidence_ids"]:
            raise ValueError("Paper-derived related work requires evidence")
    for group in (paper["contributions"], paper["figures"]):
        ids = [item["id"] for item in group]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate contribution/figure IDs")
    for claim in paper["experiments"]["main_results"] + paper["limitations"]:
        if not claim["evidence_ids"]:
            raise ValueError("Results and paper-stated limitations require evidence")
    for figure in paper["figures"]:
        if published and not re.fullmatch(
            rf"/paper-assets/{re.escape(paper['id'])}/[a-zA-Z0-9_-]+\.webp", figure["path"]):
            raise ValueError("Figure path must refer to a published WebP in this paper's directory")


def normalize_title(title: str) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", title).casefold())


def identity(metadata: dict) -> str:
    doi = re.sub(r"^https?://(?:dx\.)?doi.org/", "", metadata.get("doi", "").strip(), flags=re.I)
    if doi:
        return "doi:" + doi.casefold()
    arxiv = metadata.get("arxiv_id", "").strip()
    if arxiv:
        return "arxiv:" + re.sub(r"v\d+$", "", arxiv)
    return "title:" + normalize_title(metadata["title"]) + ":" + str(metadata.get("year"))


def same_zotero(first: dict, second: dict) -> bool:
    a, b = first.get("zotero", {}), second.get("zotero", {})
    return bool(a.get("item_key") and a.get("item_key") == b.get("item_key")
                and a.get("library_id") == b.get("library_id"))


def choose_id(metadata: dict, records: list[dict], source_hash: str, requested: str | None = None) -> str:
    if requested and (not ID_RE.fullmatch(requested) or requested in {"index", "tags"}):
        raise ValueError("Invalid --id; use lowercase letters, digits and hyphens")
    # Existing DOI/arXiv/hash is authoritative: later title corrections cannot change its slug.
    matches = [p for p in records if identity(p["metadata"]) == identity(metadata)
               or p["analysis_meta"]["source_hash"] == source_hash
               or same_zotero(p["metadata"], metadata)]
    if len(matches) > 1:
        raise ValueError("Ambiguous existing identity; resolve duplicate records first")
    if matches:
        if requested and requested != matches[0]["id"]:
            raise ValueError("Existing paper must keep its stable ID")
        return matches[0]["id"]
    name = metadata.get("short_title") or metadata["title"]
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:72].rstrip("-") or "paper"
    slug += f"-{metadata['year']}" if metadata.get("year") else ""
    result = requested or slug
    if result in {"index", "tags"}:
        result = "paper-" + result
    occupied = {p["id"] for p in records}
    if result in occupied:
        if requested:
            raise ValueError("Requested ID belongs to a different paper")
        result += "-" + hashlib.sha256(identity(metadata).encode()).hexdigest()[:8]
    if result in occupied:
        raise ValueError("Paper ID collision")
    return result


def build_index(records: list[dict], manual_tags: dict | None = None) -> dict:
    from .tags import EMPTY, tag_names
    config = manual_tags if manual_tags is not None else EMPTY
    rows = []
    for paper in sorted(records, key=lambda p: (-(p["metadata"]["year"] or 0), p["id"])):
        meta = paper["metadata"]
        rows.append({"id": paper["id"], **{key: meta[key] for key in
                    ("title", "short_title", "authors", "year", "venue", "doi", "arxiv_id")},
                    "tags": tag_names(config, paper["id"]), "one_sentence": paper["overview"]["one_sentence"],
                    "thumbnail": next((f["path"] for f in paper["figures"] if f["type"] in
                                       {"concept_overview", "architecture", "method_overview"}), ""),
                    "updated_at": paper["analysis_meta"]["analyzed_at"]})
    return {"schema_version": SCHEMA_VERSION, "papers": rows}


class PaperStore:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.data = self.root / "data/papers"
        self.assets = self.root / "public/paper-assets"
        self.journal = self.data / ".transaction.json"

    def _allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        return (resolved == (self.root / "data/paper-tags.json").resolve()
                or resolved.parent == self.data.resolve() and path.suffix == ".json" and not path.name.startswith(".")
                or resolved.is_relative_to(self.assets.resolve()) and path.suffix == ".webp")

    @contextlib.contextmanager
    def lock(self):
        self.data.mkdir(parents=True, exist_ok=True)
        with (self.data / ".write.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            self.recover()
            yield

    def recover(self):
        if self.journal.exists():
            entries = json.loads(self.journal.read_text())
            for entry in entries:
                path = self.root / entry["path"]
                if not self._allowed(path):
                    raise ValueError("Unsafe transaction recovery target")
                if entry["previous"] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, base64.b64decode(entry["previous"]))
            self.journal.unlink()

    def records(self) -> list[dict]:
        papers = []
        for path in sorted(self.data.glob("*.json")):
            if path.name == "index.json" or path.name.startswith("."):
                continue
            paper = json.loads(path.read_text())
            validate(paper)
            if path.stem != paper["id"]:
                raise ValueError(f"Filename/ID mismatch: {path.name}")
            for figure in paper["figures"]:
                if not (self.root / "public" / figure["path"].lstrip("/")).is_file():
                    raise ValueError(f"Missing figure asset for {paper['id']}")
            papers.append(paper)
        return papers

    def manual_tags(self) -> dict:
        from .tags import read_tags
        return read_tags(self.root / "data/paper-tags.json")

    def commit(self, changes: dict[Path, bytes | None]):
        entries = []
        for path in changes:
            if not self._allowed(path):
                raise ValueError("Unsafe publication target")
            entries.append({"path": str(path.relative_to(self.root)),
                            "previous": base64.b64encode(path.read_bytes()).decode() if path.exists() else None})
        atomic_write(self.journal, encode(entries))
        try:
            for path, content in changes.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, content)
            self.journal.unlink()
        except BaseException:
            self.recover()
            raise

    def publish(self, paper: dict, images: dict[str, bytes], *, update: bool = False):
        validate(paper)
        with self.lock():
            records = self.records()
            old = next((p for p in records if p["id"] == paper["id"]), None)
            if old and not update:
                raise ValueError("Paper already exists; pass --update (even for cached analysis)")
            if old and not (identity(old["metadata"]) == identity(paper["metadata"])
                            or old["analysis_meta"]["source_hash"] == paper["analysis_meta"]["source_hash"]
                            or same_zotero(old["metadata"], paper["metadata"])):
                raise ValueError("Concurrent ID conflict: refusing to replace a different paper")
            changes = {self.root / "public" / path.lstrip("/"): data for path, data in images.items()}
            for figure in paper["figures"]:
                if figure["path"] not in images:
                    raise ValueError("Selected figure has no generated asset")
            # Delete only previously referenced generated files, never a broad directory.
            for figure in old["figures"] if old else []:
                if figure["path"] not in images:
                    changes[self.root / "public" / figure["path"].lstrip("/")] = None
            changes[self.data / f"{paper['id']}.json"] = encode(paper)
            records = [p for p in records if p["id"] != paper["id"]] + [paper]
            changes[self.data / "index.json"] = encode(build_index(records, self.manual_tags()))
            self.commit(changes)

    def rebuild_index(self) -> dict:
        with self.lock():
            index = build_index(self.records(), self.manual_tags())
            self.commit({self.data / "index.json": encode(index)})
            return index
