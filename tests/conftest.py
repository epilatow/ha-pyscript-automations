"""Pytest configuration - runs before test collection."""
# This is AI generated code

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Repository root
_REPO_ROOT = Path(__file__).parent.parent

# Ensure pyscript/modules is importable
sys.path.insert(0, str(_REPO_ROOT / "pyscript" / "modules"))


# Create a temporary directory for __pycache__ and redirect
# all bytecode there
_pycache_tmpdir = tempfile.mkdtemp(prefix="pytest_pycache_")
sys.pycache_prefix = _pycache_tmpdir

# Also prevent bytecode writing for subsequent imports
# (belt and suspenders)
sys.dont_write_bytecode = True


def _cleanup_all_caches() -> None:
    """Remove temp dirs and any cache dirs in the repo."""
    # Clean up the temp pycache directory
    shutil.rmtree(_pycache_tmpdir, ignore_errors=True)
    # Clean up any __pycache__ created before
    # sys.pycache_prefix was set (e.g., conftest.py's
    # own bytecode)
    for pycache in _REPO_ROOT.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
    # Clean up any .mypy_cache directories
    for mypy_cache in _REPO_ROOT.rglob(".mypy_cache"):
        if mypy_cache.is_dir():
            shutil.rmtree(mypy_cache, ignore_errors=True)


# Register cleanup for when Python exits
atexit.register(_cleanup_all_caches)


def pytest_sessionfinish(
    session,  # type: ignore[no-untyped-def]
    exitstatus,  # type: ignore[no-untyped-def]
) -> None:
    """Clean up pycache directories after test session."""
    _cleanup_all_caches()


def run_tests(
    test_file: str,
    script_path: Path,
    repo_root: Path,
) -> None:
    """Entry point for running a test file directly.

    Handles ``--verbose`` and ``--coverage`` flags, then
    invokes ``pytest.main()``.  Called from each test
    file's ``__main__`` block.

    Args:
        test_file: The test file's ``__file__`` path.
        script_path: Path to the script under test
            (used to derive the coverage module name).
        repo_root: Repository root directory.
    """
    import argparse

    import pytest  # type: ignore[import-not-found]

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose test output",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run with coverage report",
    )
    args = parser.parse_args()

    if args.coverage:
        # Run as subprocess so coverage tracks from the
        # start, including import-time lines (class defs,
        # function signatures, etc.) that are missed when
        # using pytest.main() in-process.
        import subprocess

        module = script_path.stem.replace("-", "_")
        cov_dir = Path(tempfile.gettempdir())
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            test_file,
            "-p",
            "no:cacheprovider",
            f"--cov={module}",
            "--cov-report=term-missing",
            f"--cov-report=html:{cov_dir / (module + '_htmlcov')}",
        ]
        if args.verbose:
            cmd.append("-v")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(script_path.parent)
        env["COVERAGE_FILE"] = str(cov_dir / f"{module}.coverage")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(cmd)
        # Clean up .coverage file left by subprocess
        cov_file = Path.cwd() / ".coverage"
        if cov_file.exists():
            cov_file.unlink()
        raise SystemExit(result.returncode)

    pytest_args = [test_file, "-p", "no:cacheprovider"]
    if args.verbose:
        pytest_args.append("-v")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(pytest.main(pytest_args))


class CodeQualityBase:
    """Base for per-file code quality checks.

    Subclass as ``TestCodeQuality`` in each test file
    and set ``ruff_targets`` and ``mypy_targets`` to
    the files that test file covers.
    """

    ruff_targets: list[str] = []
    mypy_targets: list[str] = []

    def test_ruff_lint(self) -> None:
        """Ruff linter passes on all target files."""
        for target in self.ruff_targets:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    target,
                ],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"ruff lint failed on {target}."
                ' Run "uvx ruff check --fix ."'
                " to auto-fix.\n\n"
                f"{result.stdout}{result.stderr}"
            )

    def test_ruff_format(self) -> None:
        """Ruff formatting passes on all target files."""
        for target in self.ruff_targets:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "format",
                    "--check",
                    target,
                ],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"ruff format failed on {target}."
                ' Run "uvx ruff format ."'
                " to auto-fix.\n\n"
                f"{result.stdout}{result.stderr}"
            )

    def test_mypy_strict(self) -> None:
        """Mypy strict passes on all target files."""
        import pytest  # type: ignore[import-not-found]

        if not self.mypy_targets:
            pytest.skip("no mypy targets")
        for target in self.mypy_targets:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mypy",
                    target,
                    "--strict",
                ],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"mypy failed on {target}.\n\n{result.stdout}{result.stderr}"
            )
