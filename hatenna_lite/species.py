# hatenna_lite/species.py — dex numbers 1–1025 to names and types.
#
# data/species.json is generated from HatennaBot's data/pokemon.csv. Spawn
# images come from Pokétwo's image server, the same source the real bot sees.
from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA = Path(__file__).parent / "data" / "species.json"
IMAGE_URL = "https://server.poketwo.io/image?species={}"
MAX_DEX = 1025


@lru_cache(maxsize=1)
def table() -> dict[int, list[str]]:
    return {int(k): v for k, v in json.loads(DATA.read_text(encoding="utf-8")).items()}


def name(dex: int) -> str:
    return table()[dex][0]


def types(dex: int) -> list[str]:
    return table()[dex][1:]


def image(dex: int) -> str:
    return IMAGE_URL.format(dex)


def lookup(query: str) -> Optional[int]:
    """'234', '#234' or 'stantler' -> 234; None if unknown."""
    q = str(query).strip().lstrip("#").lower()
    if q.isdigit():
        return int(q) if 1 <= int(q) <= MAX_DEX else None
    return next((dex for dex, row in table().items() if row[0].lower() == q), None)


def random_dex() -> int:
    return random.randint(1, MAX_DEX)
