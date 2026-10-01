"""Read-only Zotero metadata/source adapter. No writes or PDF downloads."""
from pathlib import Path
import json
import os
import re
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


class ZoteroAdapter:
    def __init__(self):
        self.local = os.environ.get("ZOTERO_LOCAL_API", "http://localhost:23119/api/users/0").rstrip("/")
        self.storage = Path(os.environ.get("ZOTERO_STORAGE_DIR", "~/Zotero/storage")).expanduser().resolve()
        self.user_id = os.environ.get("ZOTERO_USER_ID", "")
        self.api_key = os.environ.get("ZOTERO_API_KEY", "")

    def _get(self, suffix: str):
        endpoints = [(self.local, {})]
        if self.user_id.isdigit() and self.api_key:
            endpoints.append((f"https://api.zotero.org/users/{self.user_id}", {"Zotero-API-Key": self.api_key}))
        for base, credentials in endpoints:
            request = Request(base + suffix, headers={"Zotero-API-Version": "3", **credentials})
            try:
                with urlopen(request, timeout=12) as response:
                    return json.load(response)
            except (URLError, HTTPError, TimeoutError, OSError):
                continue
        raise RuntimeError("Zotero unavailable: start Zotero and enable its local API, or configure Web API metadata access; direct PDF ingest still works")

    def resolve(self, item_key: str, pdf_override: Path | None = None) -> tuple[Path, dict]:
        if not re.fullmatch(r"[A-Z0-9]{8}", item_key):
            raise ValueError("Zotero item key must be eight uppercase letters/digits")
        item = self._get(f"/items/{item_key}")
        data = item.get("data", item)
        attachments = []
        if data.get("itemType") == "attachment":
            attachments = [item]
            parent = data.get("parentItem", "")
            if not re.fullmatch(r"[A-Z0-9]{8}", parent):
                raise ValueError("Zotero attachment needs a bibliographic parent item")
            item = self._get(f"/items/{parent}")
            data = item.get("data", item)
        key = item.get("key", data.get("key", item_key))
        if not re.fullmatch(r"[A-Z0-9]{8}", key):
            raise ValueError("Invalid Zotero parent key")
        if not pdf_override and not attachments:
            attachments = self._get(f"/items/{key}/children?limit=100")
        candidates = []
        for attachment in attachments:
            record = attachment.get("data", attachment)
            if record.get("itemType") != "attachment" or record.get("contentType") != "application/pdf":
                continue
            attachment_key = attachment.get("key", record.get("key", ""))
            if not re.fullmatch(r"[A-Z0-9]{8}", attachment_key):
                continue
            local_path = record.get("path", "")
            if local_path and not local_path.startswith("storage:"):
                path = Path(local_path).expanduser()
                if path.is_absolute() and path.is_file():
                    candidates.append(path)
            else:
                filename = record.get("filename") or local_path.removeprefix("storage:")
                if filename and Path(filename).name == filename:
                    path = self.storage / attachment_key / filename
                    if path.is_file() and path.resolve().is_relative_to(self.storage):
                        candidates.append(path)
                elif (self.storage / attachment_key).is_dir():
                    candidates.extend(sorted((self.storage / attachment_key).glob("*.pdf")))
        if pdf_override:
            source = pdf_override.expanduser().resolve(strict=True)
        elif len(candidates) == 1:
            source = candidates[0]
        elif len(candidates) > 1:
            raise ValueError("Multiple Zotero PDFs found: provide an explicit PDF path with --zotero-item")
        else:
            raise ValueError("No local Zotero PDF found; sync its attachment or supply a local PDF path")
        authors = [c.get("name") or " ".join(filter(None, [c.get("firstName"), c.get("lastName")]))
                   for c in data.get("creators", []) if c.get("creatorType") == "author"]
        year = re.search(r"\b(19|20|21)\d{2}\b", data.get("date", ""))
        url = data.get("url", "")
        arxiv = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:\s*)(\d{4}\.\d{4,5}(?:v\d+)?)", url + " " + data.get("extra", ""), re.I)
        library = item.get("library", {})
        metadata = {"title": data.get("title", ""), "authors": authors,
                    "year": int(year.group()) if year else None,
                    "venue": data.get("publicationTitle") or data.get("conferenceName") or "",
                    "doi": data.get("DOI", ""), "paper_url": url,
                    "arxiv_id": arxiv.group(1) if arxiv else "",
                    "tags": [t["tag"] for t in data.get("tags", []) if t.get("tag")],
                    "zotero": {"item_key": key, "library_id": str(library.get("id") or self.user_id or "0"),
                               "collections": data.get("collections", [])}}
        return source, metadata
