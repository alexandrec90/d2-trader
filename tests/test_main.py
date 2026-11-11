import pytest

import d2_trader
from d2_trader import __main__ as cli


def test_version_is_declared():
    assert d2_trader.__version__


def test_default_command_is_the_dashboard():
    assert cli.build_parser().parse_args([]).command is None


def test_run_needs_a_real_job():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["run", "nonsense"])


def test_run_accepts_each_job():
    for job in ("seasons", "catalog", "diablo2io", "traderie", "rates"):
        assert cli.build_parser().parse_args(["run", job]).job == job


def fake_run(status):
    class Run:
        job, records, detail = "x", 0, None

    Run.status = status
    return Run


@pytest.mark.parametrize(("status", "code"), [("ok", 0), ("disabled", 0), ("blocked", 1)])
def test_exit_code_reports_the_job(monkeypatch, capsys, status, code):
    monkeypatch.setattr(cli, "run_async", lambda coroutine: coroutine.close() or fake_run(status))
    assert cli.main(["run", "seasons"]) == code
    assert status in capsys.readouterr().out


def test_serve_starts_the_app_factory(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    assert cli.main([]) == 0
    assert calls[0][0] == (cli.create_app,)
    assert calls[0][1]["factory"] is True


def test_worker_runs_the_schedule(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "run_async", lambda coroutine: ran.append(coroutine.close()))
    assert cli.main(["worker"]) == 0
    assert len(ran) == 1


def test_run_async_finishes_a_coroutine():
    async def answer():
        return 42

    assert cli.run_async(answer()) == 42


async def test_a_named_job_runs_end_to_end(monkeypatch, sessions, settings):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "make_sessions", lambda _engine: sessions)
    monkeypatch.setattr(cli, "make_engine", lambda _url: None)
    run = await cli.run_named_job("traderie")
    assert run.status == "disabled"
