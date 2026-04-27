#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "ruff",
#     "mypy",
#     "PyYAML",
# ]
# ///
# This is AI generated code
"""Drift test for blueprint -> rendered-doc links.

Each blueprint under
``bundled/blueprints/automation/blueprint_toolkit/``
must include a ``[Full documentation](/local/...)`` markdown
link in its description, and the target HTML file must
exist under ``bundled/www/blueprint_toolkit/docs/``.

The link points at the integration's own aiohttp static
route (registered in ``async_setup_entry``), not at HA's
``/config/www/`` handler -- see ``DEVELOPMENT.md`` for
why. dev-install users who don't load the integration
get a 404 on these links; that's a documented dev-install
limitation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
BLUEPRINTS_DIR = (
    REPO_ROOT
    / "custom_components"
    / "blueprint_toolkit"
    / "bundled"
    / "blueprints"
    / "automation"
    / "blueprint_toolkit"
)
RENDERED_DOCS_DIR = (
    REPO_ROOT
    / "custom_components"
    / "blueprint_toolkit"
    / "bundled"
    / "www"
    / "blueprint_toolkit"
    / "docs"
)
LINK_URL_PREFIX = "/local/blueprint_toolkit/docs/"

sys.path.insert(0, str(REPO_ROOT / "tests"))
from conftest import CodeQualityBase  # noqa: E402


def _expected_link(stem: str) -> str:
    return f"[Full documentation]({LINK_URL_PREFIX}{stem}.html)"


def _blueprint_paths() -> list[Path]:
    return sorted(BLUEPRINTS_DIR.glob("*.yaml"))


class _BlueprintLoader(yaml.SafeLoader):
    """SafeLoader that returns None for HA-specific tags like ``!input``."""


def _ignore_unknown_tag(
    loader: yaml.Loader,  # noqa: ARG001
    tag_suffix: str,  # noqa: ARG001
    node: yaml.Node,  # noqa: ARG001
) -> Any:
    return None


_BlueprintLoader.add_multi_constructor("!", _ignore_unknown_tag)


def _load_blueprint(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = yaml.load(fh, Loader=_BlueprintLoader)
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize(
    "blueprint_path",
    _blueprint_paths(),
    ids=lambda p: p.stem,
)
class TestBlueprintDocLinks:
    def test_description_contains_full_documentation_link(
        self,
        blueprint_path: Path,
    ) -> None:
        doc = _load_blueprint(blueprint_path)
        description = doc["blueprint"]["description"]
        # YAML folded scalars collapse internal newlines to
        # spaces, so the link sits inline with the prose.
        expected = _expected_link(blueprint_path.stem)
        assert expected in description, (
            f"{blueprint_path.name}: description is missing the "
            f"full-documentation link {expected!r}.\n\n"
            f"description: {description!r}"
        )

    def test_link_target_html_exists(
        self,
        blueprint_path: Path,
    ) -> None:
        target = RENDERED_DOCS_DIR / f"{blueprint_path.stem}.html"
        assert target.is_file(), (
            f"{blueprint_path.name}: link points at "
            f"{LINK_URL_PREFIX}{blueprint_path.stem}.html but "
            f"the rendered HTML at {target} does not exist. "
            "Run 'scripts/render_docs.py' if the markdown "
            "source exists, or rename the blueprint stem to "
            "match an existing doc."
        )


class TestBlueprintsExist:
    """Sanity check so a missing blueprint dir fails loudly."""

    def test_at_least_one_blueprint_present(self) -> None:
        paths = _blueprint_paths()
        assert paths, (
            f"no blueprint .yaml files found under {BLUEPRINTS_DIR}; "
            "the parametrize fixture would silently produce zero "
            "tests without this guard."
        )


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_blueprint_docs.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
