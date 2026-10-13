from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from susanoo.config import Config, load_config
from susanoo.registry import Registry, load_registry
from susanoo.router import Profile, load_profiles


@pytest.fixture(scope="session")
def config() -> Config:
    return load_config()


@pytest.fixture(scope="session")
def profiles() -> dict[str, Profile]:
    return load_profiles()


@pytest.fixture(scope="session")
def registry() -> Registry:
    return load_registry()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """An empty fake home directory so tests never read the real user's Claude config."""
    path = tmp_path / "home"
    path.mkdir()
    return path


def write(root: Path, rel: str, content: str | dict[str, Any]) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(content) if isinstance(content, dict) else content
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    root = tmp_path / "pyproj"
    write(
        root,
        "pyproject.toml",
        (
            '[project]\nname = "demo"\nversion = "0.1"\ndependencies = ["requests>=2"]\n'
            '[project.optional-dependencies]\ndev = ["pytest>=8", "ruff"]\n'
            '[tool.pytest.ini_options]\naddopts = "-q"\n'
        ),
    )
    write(root, "src/demo/__init__.py", "")
    write(root, "src/demo/core.py", "def add(a, b):\n    return a + b\n")
    write(
        root,
        "tests/test_core.py",
        "from demo.core import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    (root / ".git").mkdir()
    return root
