#!/usr/bin/env python3
"""The local end-to-end tier: do the parsers still understand the real sites?

Contract: `.claude/rules/local-e2e.md`. Runs the `local` pytest marker, which `addopts`
deselects everywhere else. Failures reach the fix pass as pytest's `FAILED <id>` lines,
and the same lines are kept in `logs/e2e-failures.log`. A run that collected nothing
exits non-zero, because a green from zero tests is a skip reported as a pass.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = REPO_ROOT / "logs" / "e2e-failures.log"
TESTS = "tests/test_live_sites.py"
NO_TESTS_RAN = 5  # pytest's own exit code for "collected nothing".


def pytest_command(python: str) -> list[str]:
    # `-m local` after `addopts`' `-m 'not local'`: pytest takes the last one.
    return [python, "-m", "pytest", TESTS, "-m", "local", "-q", "-rf", "--no-header", "--tb=short"]


def failure_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(("FAILED ", "ERROR "))]


def exit_code_for(pytest_code: int) -> int:
    """Zero only for a run that passed something."""
    return pytest_code if pytest_code != NO_TESTS_RAN else 1


def main() -> int:
    result = subprocess.run(
        pytest_command(sys.executable),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    ARTIFACT.parent.mkdir(exist_ok=True)
    ARTIFACT.write_text(
        "\n".join(failure_lines(result.stdout)) + "\n" + result.stdout, encoding="utf8"
    )
    return exit_code_for(result.returncode)


if __name__ == "__main__":
    sys.exit(main())
