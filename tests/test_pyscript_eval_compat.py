#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = [
#   "pytest",
#   "pytest-cov",
#   "ruff",
#   "mypy",
#   "homeassistant",
#   "croniter==6.0.0",
#   "watchdog==6.0.0",
# ]
# ///
# This is AI generated code
"""Run every pyscript file through the real PyScript AstEval.

The other ``TestPyScriptCompatibility`` class in
``test_ha_pyscript_automations.py`` uses static AST
scans to catch a curated list of known-bad patterns.
That list only grows when we get burned in production.

This test goes further: it instantiates PyScript's
real ``AstEval`` interpreter, feeds each of our
``pyscript/**/*.py`` files into it, and actually
executes every top-level statement.  Anything that
the interpreter would reject at import time under
real PyScript (e.g. lambda closure capture,
``sort(key=func)``, bare ``open()``, generator
expressions, new Python syntax PyScript hasn't
implemented) fails here too.

The static scan stays: it catches things the
evaluator only hits lazily (``yield`` inside a
function body is only flagged when the generator is
iterated), and it produces sharper file:line errors.
The two suites are complementary.

PyScript is not on PyPI.  Its source is cloned via
``git clone --depth 1 --branch <version>`` into
``/tmp/ha_pyscript_eval_cache/pyscript-<version>/``
on first run and reused thereafter.  A companion
``git fetch --depth 1 --tags`` pulls the full set
of upstream release tags into the clone so the pin
check can run offline.

The cache is considered stale after one week: on
the next run we blow it away and re-clone, which
refreshes both the source and the tag list in one
shot.  ``TestPyScriptPin`` then compares the pinned
version against the latest ``vX.Y.Z`` tag present
in the local clone and fails loudly if upstream has
moved on, so tests don't silently drift behind the
version HA pulls in production.
"""

import asyncio
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest  # type: ignore[import-not-found]

REPO_ROOT = Path(__file__).parent.parent

# run_tests() uses this to derive the --cov module
# name.  The evaluator covers every pyscript file,
# so pick the service wrapper as a representative
# entry point.
_SCRIPT_PATH = REPO_ROOT / "pyscript" / "ha_pyscript_automations.py"

PYSCRIPT_VERSION = "1.7.0"
_PYSCRIPT_REPO_URL = "https://github.com/custom-components/pyscript.git"
_CACHE_DIR = Path("/tmp/ha_pyscript_eval_cache")
_PYSCRIPT_CLONE = _CACHE_DIR / f"pyscript-{PYSCRIPT_VERSION}"
_CLONE_COMPLETE_MARKER = _PYSCRIPT_CLONE / ".clone_complete"
_CLONE_MAX_AGE_SECS = 7 * 86400
_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _is_pyscript_clone_valid(path: Path) -> bool:
    """Return True if ``path`` holds a usable pyscript clone.

    Two checks are necessary: the expected source file
    must be present (catches interrupted checkouts that
    left some files but not all), and ``git rev-parse
    --verify HEAD`` must succeed (catches interrupted
    clones where the working tree exists but the git
    metadata was never finalized).  Either check alone
    can pass over a half-broken cache.
    """
    marker = path / "custom_components" / "pyscript" / "eval.py"
    if not marker.is_file():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
    )
    return result.returncode == 0


def _clone_is_fresh(path: Path) -> bool:
    """True if the clone is valid, complete, and under a week old.

    ``.clone_complete`` is touched at the very end of
    ``_ensure_pyscript_clone``; its presence proves
    both the source clone and the follow-up tag fetch
    succeeded.  Its mtime drives the weekly refresh --
    once a week we treat the cache as stale, wipe it,
    and re-clone so the tag set and source both pick
    up any new upstream releases.
    """
    if not _is_pyscript_clone_valid(path):
        return False
    if not _CLONE_COMPLETE_MARKER.is_file():
        return False
    try:
        age = time.time() - _CLONE_COMPLETE_MARKER.stat().st_mtime
    except OSError:
        return False
    return age < _CLONE_MAX_AGE_SECS


def _ensure_pyscript_clone() -> Path:
    """Clone pyscript at the pinned tag if cache is stale or missing.

    The cache is refreshed when any of these hold:
      * no clone on disk;
      * the clone is half-broken (files present but
        git metadata missing, or vice versa);
      * the ``.clone_complete`` marker is missing
        (clone succeeded but the tag fetch did not);
      * the marker is more than a week old.

    A fresh clone re-runs both ``git clone`` (pinned
    tag source) and ``git fetch --tags`` (full tag
    list for the offline pin check).
    """
    if _clone_is_fresh(_PYSCRIPT_CLONE):
        return _PYSCRIPT_CLONE
    if _PYSCRIPT_CLONE.exists():
        shutil.rmtree(_PYSCRIPT_CLONE)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            PYSCRIPT_VERSION,
            _PYSCRIPT_REPO_URL,
            str(_PYSCRIPT_CLONE),
        ],
        check=True,
        timeout=180,
    )
    # Pull every remote tag at depth 1 so the pin check
    # can enumerate upstream releases from the local
    # clone without another network round-trip on each
    # test run.  Refreshed weekly alongside the clone.
    subprocess.run(
        [
            "git",
            "-C",
            str(_PYSCRIPT_CLONE),
            "fetch",
            "--depth",
            "1",
            "--tags",
        ],
        check=True,
        timeout=120,
    )
    if not _is_pyscript_clone_valid(_PYSCRIPT_CLONE):
        raise RuntimeError(
            f"pyscript clone at {_PYSCRIPT_CLONE} is"
            " invalid after a fresh git clone; check"
            " git configuration and retry."
        )
    # Written last so its presence proves every step
    # above completed -- ``_clone_is_fresh`` treats a
    # missing marker as a stale cache even if the
    # source files look intact.
    _CLONE_COMPLETE_MARKER.touch()
    return _PYSCRIPT_CLONE


def _latest_upstream_tag(path: Path) -> str:
    """Return the highest ``vX.Y.Z`` tag in the local clone.

    Scans ``git tag -l`` output from the cache, skips
    anything that doesn't match ``vX.Y.Z`` (so we
    ignore ``1.8.0rc1`` and friends -- HACS only ships
    stable releases), and returns the highest semver
    tuple with any leading ``v`` stripped so it can
    be compared directly against ``PYSCRIPT_VERSION``.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "tag", "-l"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    best_version: tuple[int, int, int] | None = None
    best_name = ""
    for raw in result.stdout.splitlines():
        tag = raw.strip()
        match = _TAG_RE.match(tag)
        if match is None:
            continue
        ver = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        if best_version is None or ver > best_version:
            best_version = ver
            best_name = tag.lstrip("v")
    if best_version is None:
        raise RuntimeError(
            f"no vX.Y.Z tags found in pyscript clone at"
            f" {path}; the tag fetch must have been"
            f" incomplete."
        )
    return best_name


_PYSCRIPT_DIR = _ensure_pyscript_clone()
# Importing ``custom_components.pyscript.*`` requires
# the clone root on sys.path.
sys.path.insert(0, str(_PYSCRIPT_DIR))
# Inter-module imports inside a pyscript file (e.g.
# ``from helpers import ...`` inside
# ha_pyscript_automations.py) fall through to
# importlib via the allow_all_imports path, so
# pyscript/modules/ must be reachable by name.
sys.path.insert(0, str(REPO_ROOT / "pyscript" / "modules"))

from custom_components.pyscript.eval import AstEval  # noqa: E402
from custom_components.pyscript.function import Function  # noqa: E402
from custom_components.pyscript.global_ctx import (  # noqa: E402
    GlobalContext,
)

# DecoratorRegistry only exists on pyscript master
# (post-1.7.0).  1.7.0 handles @service via different
# infrastructure that doesn't need an init() call.
try:
    from custom_components.pyscript.decorator import (  # noqa: E402
        DecoratorRegistry,
    )
except ImportError:
    DecoratorRegistry = None  # type: ignore[assignment,misc]

from conftest import CodeQualityBase  # noqa: E402


class _FakeConfigEntry:
    """Minimal ConfigEntry stand-in for pyscript internals.

    ``AstEval.__init__`` and ``GlobalContext.__init__``
    both read ``config_entry.data.get(...)``, so the
    ``data`` attribute is the only surface we need.
    ``allow_all_imports`` routes imports through
    ``importlib`` instead of pyscript's allow-list.
    ``data`` is built per instance so pyscript writing
    through it can't leak between tests.
    """

    def __init__(self) -> None:
        self.data: dict[str, object] = {"allow_all_imports": True}


def _make_fake_hass() -> MagicMock:
    """Build the minimal hass surface pyscript reads."""
    hass = MagicMock()
    hass.data = {"pyscript": {"config_entry": _FakeConfigEntry()}}

    async def _aae(func: Any, *args: Any, **kwargs: Any) -> Any:
        # pyscript normally dispatches to a real HA
        # executor here; for tests just run inline.
        return func(*args, **kwargs)

    hass.async_add_executor_job = _aae
    return hass


Function.hass = _make_fake_hass()
# Pyscript master moved @service / @pyscript_executor
# / @state_trigger through a DecoratorRegistry that
# needs init() before any function definition is
# evaluated.  On 1.7.0 the registry doesn't exist yet
# and the older resolution path needs no setup.
if DecoratorRegistry is not None:
    DecoratorRegistry.init(Function.hass)


def _eval_source(
    name: str,
    source: str,
    filename: str = "<test>",
) -> None:
    """Parse and execute ``source`` under AstEval.

    Every top-level statement is dispatched through
    ``aeval``, which is what PyScript does when it
    loads a module.  Imports are routed through the
    standard ``importlib`` via ``allow_all_imports``
    rather than pyscript's module loader (which would
    need a running HA instance).
    """
    gc = GlobalContext(name, global_sym_table={})

    async def _noop_module_import(
        mod_name: str,
        level: int,
    ) -> tuple[None, None]:
        # Forces ast_import/ast_importfrom to fall
        # through to importlib.import_module via the
        # allow_all_imports path.
        #
        # VERSION COUPLING: this return shape is
        # pyscript-version-specific.  1.7.0's
        # ast_import does
        # ``mod, err_ctx = await module_import(...)``
        # and expects the 2-tuple.  Master dropped
        # the error_ctx return and expects a bare
        # module-or-None.  When bumping
        # PYSCRIPT_VERSION, re-check ast_import in
        # the new source and adjust this return.
        # The positive sanity test
        # ``test_stdlib_import_and_use_work`` will
        # fail loudly if the shape becomes wrong.
        return (None, None)

    gc.module_import = _noop_module_import  # type: ignore[method-assign]
    ae = AstEval(name, gc)
    ae.parse(source, filename=filename)

    short_name = Path(filename).name

    async def _run() -> None:
        for node in ae.ast.body:
            lineno = getattr(node, "lineno", "?")
            try:
                await ae.aeval(node)
            except Exception as exc:
                # Many pyscript errors (notably
                # NotImplementedError from ast_not_*
                # handlers) surface with no file:line
                # context of their own.  Attach ours
                # so test output points at the
                # offending source, not just into
                # pyscript internals.
                exc.add_note(f"(in {short_name} at line {lineno})")
                raise
            # pyscript 1.7.0 swallows exceptions raised
            # inside user functions (from the lambda
            # closure body, from pyscript_compile'd
            # code, etc.) into ae.exception_obj rather
            # than letting them propagate.  Real HA
            # logs these via log_exception() and the
            # call fails; we surface them by raising.
            if ae.exception_obj is not None:
                ae.exception_obj.add_note(f"(in {short_name} at line {lineno})")
                raise ae.exception_obj

    asyncio.run(_run())


_PYSCRIPT_FILES = sorted(
    p for p in (REPO_ROOT / "pyscript").rglob("*.py") if p.name != "__init__.py"
)


class TestEvaluates:
    """Every pyscript file must be acceptable to AstEval."""

    @pytest.mark.parametrize(
        "path",
        _PYSCRIPT_FILES,
        ids=[p.name for p in _PYSCRIPT_FILES],
    )
    def test_evaluates(self, path: Path) -> None:
        _eval_source(
            f"modules.{path.stem}",
            path.read_text(),
            filename=str(path),
        )


class TestHarnessSanity:
    """Verify the harness itself still works as intended.

    ``TestEvaluates`` is only meaningful if the harness
    rejects what real pyscript rejects and accepts what
    real pyscript accepts.  A pyscript upgrade or a
    change to our mocking can silently move that line
    in either direction -- these checks anchor both.

    Negative checks: one per ``test_no_*`` ban in
    ``TestPyScriptCompatibility``, feeding the banned
    construct through the real evaluator and asserting
    it raises.  If one stops raising, the cause is
    either (a) our mocking has started masking a real
    failure, or (b) pyscript upstream now accepts that
    construct and the static ban can be removed -- both
    require investigation, and the paired test is what
    surfaces the signal.

    Positive checks: known-good patterns must succeed
    (in particular, the import fallback that routes
    around pyscript's HA-coupled module loader).  If
    these start failing, our mocking is over-strict
    and TestEvaluates might be rejecting valid code.

    Both directions are needed because the failure
    modes differ: e.g. a signature change to
    ``module_import`` between pyscript releases
    silently poisons the import path without
    tripping any of the negative checks.
    """

    # -- Negative: pyscript must reject these --

    def test_generator_expression_raises(self) -> None:
        # PyScript never implemented ast_generatorexp,
        # so any generator expression raises
        # NotImplementedError from the dispatch table.
        source = "x = sum(i for i in range(10))\n"
        with pytest.raises(NotImplementedError):
            _eval_source("negative_genexp", source)

    def test_lambda_closure_capture_raises(self) -> None:
        # Lambda bodies are compiled as native Python
        # via @pyscript_compile, so they can't see the
        # enclosing pyscript function's local frame.
        # Verified on production HA (pyscript 1.7.0):
        # calling the lambda raises NameError on the
        # captured name.  This is the specific runtime
        # failure the static suite's blanket lambda
        # ban protects against.
        source = (
            "def outer():\n"
            "    val = 5\n"
            "    f = lambda x: x + val\n"
            "    return f(1)\n"
            "outer()\n"
        )
        with pytest.raises(NameError):
            _eval_source("negative_lambda", source)

    @pytest.mark.filterwarnings(
        "ignore:coroutine '.*' was never awaited:RuntimeWarning"
    )
    def test_sort_key_function_raises(self) -> None:
        # PyScript wraps key(x) as a coroutine, so the
        # subsequent comparison raises TypeError.  The
        # filterwarnings silences the unawaited-coroutine
        # RuntimeWarning pyscript emits as a side effect
        # when the comparison blows up mid-sort.
        source = (
            "def key_fn(x):\n"
            "    return x\n"
            "items = [3, 1, 2]\n"
            "items.sort(key=key_fn)\n"
        )
        with pytest.raises(TypeError):
            _eval_source("negative_sort_key", source)

    def test_bare_open_raises(self) -> None:
        # PyScript strips ``open`` from builtins, so a
        # name lookup fails before the call happens.
        source = "_ = open\n"
        with pytest.raises(NameError):
            _eval_source("negative_bare_open", source)

    def test_classmethod_decorator_raises(self) -> None:
        # PyScript wraps functions as EvalFunc instances
        # that the classmethod descriptor doesn't know
        # how to bind, so calling ``C.f()`` raises
        # TypeError: 'EvalFunc' object is not callable.
        source = (
            "class C:\n"
            "    @classmethod\n"
            "    def f(cls):\n"
            "        return 1\n"
            "C.f()\n"
        )
        with pytest.raises(TypeError):
            _eval_source("negative_classmethod", source)

    def test_property_decorator_raises(self) -> None:
        # Same root cause as classmethod: the property
        # descriptor can't invoke an EvalFunc-wrapped
        # method, so attribute access raises TypeError.
        source = (
            "class C:\n"
            "    @property\n"
            "    def x(self):\n"
            "        return 1\n"
            "C().x\n"
        )
        with pytest.raises(TypeError):
            _eval_source("negative_property", source)

    def test_yield_raises(self) -> None:
        # PyScript never implemented ast_yield.  The
        # dispatch miss raises NotImplementedError as
        # soon as the generator is iterated -- using
        # ``next(gen)`` forces that immediately.
        source = "def g():\n    yield 1\ngen = g()\nnext(gen)\n"
        with pytest.raises(NotImplementedError):
            _eval_source("negative_yield", source)

    def test_match_case_raises(self) -> None:
        # No ast_match handler in 1.7.0; NotImplementedError
        # fires when the match statement is reached.
        source = (
            "def f(x):\n"
            "    match x:\n"
            "        case 1:\n"
            "            return 1\n"
            "    return 0\n"
            "f(1)\n"
        )
        with pytest.raises(NotImplementedError):
            _eval_source("negative_match", source)

    def test_type_checking_local_annotation_raises(self) -> None:
        # PyScript evaluates function-body variable
        # annotations at runtime (unlike standard
        # Python, which skips them per PEP 526).  A
        # name imported only under ``if TYPE_CHECKING:``
        # is undefined at runtime and resolving the
        # annotation raises NameError.
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from collections.abc import Mapping\n"
            "def f():\n"
            "    m: Mapping[str, int] = {'a': 1}\n"
            "    return m\n"
            "f()\n"
        )
        with pytest.raises(NameError):
            _eval_source("negative_type_checking_local", source)

    def test_print_raises(self) -> None:
        # PyScript strips ``print`` from builtins so
        # authors use ``log.warning`` instead (print
        # output would otherwise be captured by the HA
        # logger in a surprising way).  The name lookup
        # fails before the call happens.
        source = 'print("hi")\n'
        with pytest.raises(NameError):
            _eval_source("negative_print", source)

    # -- Positive: pyscript must accept these --

    def test_stdlib_import_and_use_work(self) -> None:
        # Exercises the ``_noop_module_import`` ->
        # ``importlib.import_module`` fallback both
        # for ``import X`` and ``from X import Y``,
        # then actually *uses* the imported names.
        # Using them is the critical step: if
        # ``module_import``'s return shape drifts
        # between pyscript versions, the name binding
        # will silently hold a wrong value (e.g. a
        # ``(None, None)`` tuple) and the use of it
        # below will fail with a clear AttributeError
        # pointing at this test.  Without this
        # check, a signature drift would only show
        # up as cryptic failures in TestEvaluates.
        source = (
            "import json\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Point:\n"
            "    x: int\n"
            "    y: int\n"
            "p = Point(1, 2)\n"
            "blob = json.dumps({'x': p.x, 'y': p.y})\n"
            'if blob != \'{"x": 1, "y": 2}\':\n'
            "    raise ValueError(\n"
            "        'harness import path broken:'\n"
            "        ' got ' + repr(blob)\n"
            "    )\n"
        )
        _eval_source("positive_imports", source)


class TestPyScriptPin:
    """Ensure the pinned pyscript version matches upstream.

    Production runs whatever version HA pulls via
    HACS -- normally the latest release.  If this pin
    drifts behind upstream, tests succeed against an
    older evaluator while production executes a
    newer one, so compatibility bugs in new
    releases would go undetected.

    The upstream tag list is fetched into the local
    clone at clone time (see ``_ensure_pyscript_clone``)
    and refreshed once a week when the cache ages out,
    so this check is a pure local comparison.
    """

    def test_pin_matches_latest_release(self) -> None:
        latest = _latest_upstream_tag(_PYSCRIPT_DIR)
        assert latest == PYSCRIPT_VERSION, (
            f"pyscript {latest} has been released."
            f" Bump PYSCRIPT_VERSION in"
            f" tests/{Path(__file__).name} from"
            f" {PYSCRIPT_VERSION} to {latest} and"
            f" re-run tests."
        )


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_pyscript_eval_compat.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
