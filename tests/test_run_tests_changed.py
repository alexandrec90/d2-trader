"""`scripts/run-tests.py --changed` says it is pytest's last-failed rerun.

A fixer told to run `lint-all.py --changed` ran `run-tests.py --changed` too, saw only
"run-tests: pytest ... --last-failed ..." and "passed", and could not tell which test
files that had covered (devkit e6929429).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-tests.py"


def _runner():
    spec = importlib.util.spec_from_file_location("run_tests_changed_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_changed_names_itself_the_last_failed_rerun(monkeypatch, capsys):
    rt = _runner()
    seen: list[list[str]] = []
    monkeypatch.setattr(rt, "rerun_in_tree_venv", lambda module, argv: None)
    monkeypatch.setattr(rt, "run_pytest", lambda cmd: seen.append(cmd) or "")
    monkeypatch.setattr(rt, "_finish", lambda failures: 0)
    assert rt.main(["--changed"]) == 0
    assert "--last-failed" in seen[0]
    out = capsys.readouterr().out
    assert rt.LAST_FAILED_NOTE in out and "bare" in out


def test_a_bare_run_does_not_print_the_note(monkeypatch, capsys):
    rt = _runner()
    monkeypatch.setattr(rt, "rerun_in_tree_venv", lambda module, argv: None)
    monkeypatch.setattr(rt, "_plan_changed", lambda root: None)
    monkeypatch.setattr(rt, "run_pytest", lambda cmd: "")
    monkeypatch.setattr(rt, "_finish", lambda failures: 0)
    rt.main([])
    assert rt.LAST_FAILED_NOTE not in capsys.readouterr().out
