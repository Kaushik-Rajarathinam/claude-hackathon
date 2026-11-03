# hatenna_lite/ledger.py — append-only record of governed decisions.
#
# Documents follow the Autonomous Trust v1 contracts
# (github.com/commerce-trust-labs/autonomous-trust-contracts, Apache-2.0); the
# schemas this port emits are vendored under schemas/v1. When `jsonschema` is
# installed, every contract document is validated before it is written.
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CONTRACT_VERSION = "1.0.0"
SCHEMA_DIR = Path(__file__).parent / "schemas" / "v1"
SCHEMAS = {"execution-tier-binding": "execution-tier-binding.schema.json"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validator(kind: str):
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError:
        return None
    resources = []
    for path in SCHEMA_DIR.glob("*.json"):
        doc = json.loads(path.read_text())
        res = Resource.from_contents(doc)
        resources += [(doc["$id"], res), (path.name, res)]
    schema = json.loads((SCHEMA_DIR / SCHEMAS[kind]).read_text())
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.validators = {k: _validator(k) for k in SCHEMAS}

    def write(self, kind: str, doc: dict) -> dict:
        v = self.validators.get(kind)
        if v is not None:
            v.validate(doc)  # a port that drifts from the spec fails loudly
        with self.path.open("a") as f:
            f.write(json.dumps({"record": kind, **doc}) + "\n")
        return doc

    def read(self, kind: Optional[str] = None) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text().splitlines() if line]
        return [r for r in rows if kind is None or r["record"] == kind]
