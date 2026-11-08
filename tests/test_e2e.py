import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("e2e", ROOT / "scripts" / "e2e.py")
assert SPEC is not None
assert SPEC.loader is not None
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)


def test_command_selects_the_local_tier():
    command = e2e.pytest_command("python")
    assert command[command.index("-m", 2) + 1] == "local"
    assert e2e.TESTS in command


def test_only_failure_lines_are_kept():
    output = "...\nFAILED tests/x.py::test_a - boom\nERROR tests/y.py\n1 failed\n"
    assert e2e.failure_lines(output) == ["FAILED tests/x.py::test_a - boom", "ERROR tests/y.py"]


def test_a_run_of_nothing_is_red():
    assert e2e.exit_code_for(e2e.NO_TESTS_RAN) == 1


def test_real_exit_codes_pass_through():
    assert e2e.exit_code_for(0) == 0
    assert e2e.exit_code_for(1) == 1


def test_main_reports_and_writes_the_artifact(monkeypatch, tmp_path):
    class Result:
        stdout = "FAILED tests/x.py::test_a\n"
        stderr = ""
        returncode = 1

    artifact = tmp_path / "logs" / "e2e.log"
    monkeypatch.setattr(e2e, "ARTIFACT", artifact)
    monkeypatch.setattr(e2e.subprocess, "run", lambda *a, **k: Result)
    assert e2e.main() == 1
    assert "FAILED tests/x.py::test_a" in artifact.read_text(encoding="utf8")
