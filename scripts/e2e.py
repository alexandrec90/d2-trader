#!/usr/bin/env python3
"""The end-to-end tiers: do the parsers still understand the real sites?

Contract: `.claude/rules/local-e2e.md`. Two markers, both deselected by `addopts`:

- `local` (the default run): the live-site tests that must come from this workstation.
  diablo2.io sits behind Cloudflare and asks for a slow pace, so it is fetched from here,
  nightly, and never from a datacenter runner on every merge.
- `ci_e2e` (`--ci`): the live-site tests a hosted runner can run -- Blizzard's public news
  feed. The PR Gate's `E2E` job runs them on the default branch.

Failures reach the fix pass as pytest's `FAILED <id>` lines, and the same lines are kept
in `logs/e2e-failures.log`. A run that collected nothing exits non-zero, because a green
from zero tests is a skip reported as a pass, and under `DEVKIT_E2E` a skip is a failure.

Usage:
    python scripts/e2e.py         # the local tier, on this workstation
    python scripts/e2e.py --ci    # the tier a hosted runner runs
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = REPO_ROOT / "logs" / "e2e-failures.log"
LOCAL_MARKER = "local"
CI_MARKER = "ci_e2e"
NO_TESTS_RAN = 5  # pytest's own exit code for "collected nothing".
# `-rs` prints one of these per skip: `SKIPPED [1] tests/x.py:12: reason`.
SKIPPED_LINE = re.compile(r"^SKIPPED \[\d+\] (?P<where>\S+?):? (?P<reason>.*)$")


def marker_for(argv: Sequence[str]) -> str:
    """The marker the run selects: `ci_e2e` under `--ci`, else `local`."""
    return CI_MARKER if "--ci" in argv else LOCAL_MARKER


def pytest_command(python: str, marker: str = LOCAL_MARKER) -> list[str]:
    # `-m <marker>` after `addopts`' `-m '...'`: pytest takes the last one. No path, so
    # `testpaths` is collected and the marker alone decides what runs.
    return [python, "-m", "pytest", "-m", marker, "-q", "-rfEs", "--no-header", "--tb=short"]


def skip_failures(output: str) -> list[str]:
    """Each skip pytest reported, as a `FAILED <id>` line: under `DEVKIT_E2E` a skip is red."""
    lines = []
    for line in output.splitlines():
        match = SKIPPED_LINE.match(line)
        if match:
            lines.append(f"FAILED {match['where']} - skipped under DEVKIT_E2E: {match['reason']}")
    return lines


def failure_lines(output: str, env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    failed = [line for line in output.splitlines() if line.startswith(("FAILED ", "ERROR "))]
    return failed + (skip_failures(output) if env.get("DEVKIT_E2E") else [])


def exit_code_for(pytest_code: int, skipped_under_e2e: bool = False) -> int:
    """Zero only for a run that passed something and, under `DEVKIT_E2E`, skipped nothing."""
    if pytest_code == NO_TESTS_RAN:
        return 1
    return pytest_code or (1 if skipped_under_e2e else 0)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    result = subprocess.run(
        pytest_command(sys.executable, marker_for(args)),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = failure_lines(result.stdout)
    skips = [line for line in lines if "skipped under DEVKIT_E2E" in line]
    sys.stdout.write(result.stdout)
    sys.stdout.write("".join(f"{line}\n" for line in skips))
    sys.stderr.write(result.stderr)
    ARTIFACT.parent.mkdir(exist_ok=True)
    ARTIFACT.write_text("\n".join(lines) + "\n" + result.stdout, encoding="utf8")
    return exit_code_for(result.returncode, bool(skips))


if __name__ == "__main__":
    sys.exit(main())
