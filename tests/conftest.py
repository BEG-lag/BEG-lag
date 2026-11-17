"""Make scripts/today.py importable and serve recorded SIDOF responses."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import today


class RecordedSidof:
    """A fetch function that answers from tests/fixtures (recorded on 2026-11-17)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, path: str) -> dict:
        self.calls.append(path)
        fixture = FIXTURES / (path.strip("/").replace("/", "__") + ".json")
        if not fixture.exists():
            raise AssertionError(f"no recorded response for {path}")
        return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.fixture
def recorded() -> RecordedSidof:
    return RecordedSidof()


@pytest.fixture(autouse=True)
def _no_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """A copy of the two READMEs with empty markers, plus a previous card."""
    for name in ("README.md", "README.es.md"):
        text = (
            "# Profile\n\nIntro.\n\n### Today\n\n"
            f"{today.START}\nold block\n{today.END}\n\n## Law, compiled\n"
        )
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "today.svg").write_text("<svg>previous card</svg>\n", encoding="utf-8")
    return tmp_path
