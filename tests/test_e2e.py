import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("e2e", ROOT / "scripts" / "e2e.py")
assert SPEC is not None
assert SPEC.loader is not None
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)

SKIP_OUTPUT = "SKIPPED [1] tests/test_live_sites.py:30: no network\n1 skipped\n"


def selected_marker(command: list[str]) -> str:
    return command[command.index("-m", 2) + 1]


def test_command_selects_the_local_tier_by_default():
    assert selected_marker(e2e.pytest_command("python")) == "local"


def test_command_selects_the_ci_tier():
    assert selected_marker(e2e.pytest_command("python", e2e.CI_MARKER)) == "ci_e2e"


def test_command_names_no_path_so_the_marker_alone_selects():
    command = e2e.pytest_command("python")
    assert not any(part.startswith("tests") for part in command)


def test_ci_flag_picks_the_ci_marker():
    assert e2e.marker_for(["--ci"]) == "ci_e2e"
    assert e2e.marker_for([]) == "local"


def test_both_markers_are_deselected_by_default():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf8")
    assert "addopts = \"-p devkit_temproot -m 'not local and not ci_e2e'\"" in text
    assert '"ci_e2e: ' in text
    assert '"local: ' in text


def test_only_failure_lines_are_kept():
    output = "...\nFAILED tests/x.py::test_a - boom\nERROR tests/y.py\n1 failed\n"
    assert e2e.failure_lines(output, {}) == [
        "FAILED tests/x.py::test_a - boom",
        "ERROR tests/y.py",
    ]


def test_a_skip_is_a_failure_line_under_devkit_e2e():
    assert e2e.failure_lines(SKIP_OUTPUT, {"DEVKIT_E2E": "1"}) == [
        "FAILED tests/test_live_sites.py:30 - skipped under DEVKIT_E2E: no network"
    ]


def test_a_skip_is_not_a_failure_by_hand():
    assert e2e.failure_lines(SKIP_OUTPUT, {}) == []


def test_skip_failures_ignores_other_lines():
    assert e2e.skip_failures("1 passed\nFAILED tests/x.py::t\n") == []


def test_a_run_of_nothing_is_red():
    assert e2e.exit_code_for(e2e.NO_TESTS_RAN) == 1


def test_real_exit_codes_pass_through():
    assert e2e.exit_code_for(0) == 0
    assert e2e.exit_code_for(1) == 1


def test_a_green_run_with_a_skip_under_devkit_e2e_is_red():
    assert e2e.exit_code_for(0, skipped_under_e2e=True) == 1


def fake_pytest(monkeypatch, tmp_path, stdout: str, returncode: int):
    class Result:
        pass

    Result.stdout, Result.stderr, Result.returncode = stdout, "", returncode
    calls = []
    artifact = tmp_path / "logs" / "e2e.log"
    monkeypatch.setattr(e2e, "ARTIFACT", artifact)
    monkeypatch.setattr(e2e.subprocess, "run", lambda cmd, **k: calls.append(cmd) or Result)
    return artifact, calls


def test_main_reports_and_writes_the_artifact(monkeypatch, tmp_path):
    artifact, _ = fake_pytest(monkeypatch, tmp_path, "FAILED tests/x.py::test_a\n", 1)
    assert e2e.main([]) == 1
    assert "FAILED tests/x.py::test_a" in artifact.read_text(encoding="utf8")


def test_main_with_ci_runs_the_ci_marker(monkeypatch, tmp_path):
    _, calls = fake_pytest(monkeypatch, tmp_path, "1 passed\n", 0)
    assert e2e.main(["--ci"]) == 0
    assert selected_marker(calls[0]) == "ci_e2e"


def test_main_is_red_for_a_skip_under_devkit_e2e(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DEVKIT_E2E", "1")
    artifact, _ = fake_pytest(monkeypatch, tmp_path, SKIP_OUTPUT, 0)
    assert e2e.main(["--ci"]) == 1
    assert "FAILED tests/test_live_sites.py:30" in capsys.readouterr().out
    assert "skipped under DEVKIT_E2E" in artifact.read_text(encoding="utf8")
