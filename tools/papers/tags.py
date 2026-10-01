"""Manual taxonomy, separate from AI analysis; CLI and dev editor share this store."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jsonschema import Draft7Validator
from tools.papers.storage import ROOT, PaperStore, build_index, encode

EMPTY = {"version": "1.0", "tags": [], "papers": {}}
VALIDATOR = Draft7Validator(json.loads((ROOT / "schemas/paper-tags.schema.json").read_text()))


def normalize_tags(value: dict) -> dict:
    errors = list(VALIDATOR.iter_errors(value))
    if errors:
        raise ValueError("Invalid manual tags: " + errors[0].message)
    ids, names = set(), set()
    tags = []
    for tag in value["tags"]:
        name = tag["name"].strip()
        if not name or any(ord(c) < 32 or ord(c) == 127 for c in tag['name']):
            raise ValueError("Tag names must be nonempty and contain no control characters")
        if tag["id"] in ids or name.lower() in names:
            raise ValueError("Duplicate tag ID/name (names are case-insensitive)")
        ids.add(tag["id"])
        names.add(name.lower())
        tags.append({"id": tag["id"], "name": name})
    for assigned in value["papers"].values():
        if not set(assigned).issubset(ids):
            raise ValueError("Paper refers to an unknown tag ID")
    return {"version": "1.0", "tags": sorted(tags, key=lambda t: t["id"]),
            "papers": {key: sorted(value["papers"][key]) for key in sorted(value["papers"])}}


def read_tags(path: Path) -> dict:
    return normalize_tags(json.loads(path.read_text())) if path.exists() else normalize_tags(EMPTY)


def revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def tag_names(config: dict, paper_id: str) -> list[str]:
    names = {tag["id"]: tag["name"] for tag in config["tags"]}
    return sorted(names[tag] for tag in config["papers"].get(paper_id, []))


class PaperTagService:
    def __init__(self, root: Path = ROOT):
        self.store = PaperStore(root)
        self.path = self.store.root / "data/paper-tags.json"

    def get(self) -> dict:
        with self.store.lock():
            return {"config": read_tags(self.path), "revision": revision(self.path)}

    def save(self, config: dict, *, expected_revision: str | None = None) -> dict:
        config = normalize_tags(config)
        with self.store.lock():
            if expected_revision is not None and expected_revision != revision(self.path):
                raise ValueError("Tag config changed elsewhere. Reload before retrying; nothing was overwritten.")
            records = self.store.records()  # Validate before publishing anything.
            self.store.commit({self.path: encode(config),
                               self.store.data / "index.json": encode(build_index(records, config))})
            return {"config": config, "revision": revision(self.path)}


def main():
    parser = argparse.ArgumentParser(description="Local file-backed tag service; never runs Codex")
    parser.add_argument("command", choices=("read", "save"), help="save reads config/base_revision JSON from stdin")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        service = PaperTagService(args.root)
        if args.command == "read":
            result = service.get()
        else:
            envelope = json.load(sys.stdin)
            if set(envelope) != {"config", "base_revision"} or not isinstance(envelope["base_revision"], str):
                raise ValueError("Expected config/base_revision envelope")
            result = service.save(envelope["config"], expected_revision=envelope["base_revision"])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
