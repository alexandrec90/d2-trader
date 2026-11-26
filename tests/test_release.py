import json
import os
import signal
import subprocess
import sys
import time

import pytest

from d2_trader import release

OLD, NEW = "a" * 40, "b" * 40


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / ".git/refs/heads").mkdir(parents=True)
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf8")
    set_main(root, OLD)
    (root / "d2_trader").mkdir()
    (root / "d2_trader/app.py").write_text("one", encoding="utf8")
    (root / "pyproject.toml").write_text("deps", encoding="utf8")
    return root


def set_main(root, sha):
    (root / ".git/refs/heads/main").write_text(sha + "\n", encoding="utf8")


def edit(root):
    (root / "d2_trader/app.py").write_text("two", encoding="utf8")


def watching(root):
    return release.Watch(release.current(root), root)


def test_main_commit_reads_a_loose_ref(checkout):
    assert release.main_commit(checkout) == OLD


def test_main_commit_reads_a_packed_ref(checkout):
    (checkout / ".git/refs/heads/main").unlink()
    packed = f"# pack-refs\n{NEW} refs/heads/mainline\n{OLD} refs/heads/main\n"
    (checkout / ".git/packed-refs").write_text(packed, encoding="utf8")
    assert release.main_commit(checkout) == OLD


def test_main_commit_is_empty_with_no_ref_at_all(checkout):
    (checkout / ".git/refs/heads/main").unlink()
    assert release.main_commit(checkout) == ""


def test_main_commit_is_empty_on_another_branch(checkout):
    (checkout / ".git/HEAD").write_text("ref: refs/heads/feature\n", encoding="utf8")
    assert release.main_commit(checkout) == ""


def test_main_commit_is_empty_in_a_worktree(tmp_path):
    (tmp_path / ".git").write_text("gitdir: elsewhere", encoding="utf8")
    assert release.main_commit(tmp_path) == ""


def test_digest_ignores_bytecode_and_missing_names(checkout):
    before = release.digest(checkout, release.CODE)
    (checkout / "d2_trader/__pycache__").mkdir()
    (checkout / "d2_trader/__pycache__/app.pyc").write_bytes(b"x")
    assert release.digest(checkout, release.CODE) == before
    edit(checkout)
    assert release.digest(checkout, release.CODE) != before


def test_a_merge_that_changes_code_is_stale(checkout):
    watch = watching(checkout)
    assert not watch.stale()
    edit(checkout)
    set_main(checkout, NEW)
    assert watch.stale()


def test_an_uncommitted_edit_is_not_stale(checkout):
    watch = watching(checkout)
    edit(checkout)
    assert not watch.stale()


def test_a_merge_that_touches_no_code_is_not_stale(checkout):
    watch = watching(checkout)
    set_main(checkout, NEW)
    assert not watch.stale()
    assert watch.commit == NEW


def test_a_branch_switch_is_not_stale(checkout):
    watch = watching(checkout)
    edit(checkout)
    (checkout / ".git/HEAD").write_text("ref: refs/heads/feature\n", encoding="utf8")
    assert not watch.stale()


def test_an_unreadable_tree_waits_for_the_next_look(checkout, monkeypatch):
    watch = watching(checkout)
    set_main(checkout, NEW)

    def vanish(*_args):
        raise FileNotFoundError

    monkeypatch.setattr(release, "digest", vanish)
    assert not watch.stale()


def test_a_process_not_started_by_a_release_never_watches(checkout, monkeypatch):
    monkeypatch.delenv(release.RELEASE_ENV, raising=False)
    watch = release.started_release()
    assert not watch.stale()
    assert release.watch_in_background(watch) is None


def test_started_release_reads_the_environment(monkeypatch):
    monkeypatch.setenv(release.RELEASE_ENV, f"{OLD}:code")
    watch = release.started_release()
    assert (watch.commit, watch.code) == (OLD, "code")


def test_the_background_watch_stops_the_process_when_stale(checkout):
    watch = watching(checkout)
    edit(checkout)
    set_main(checkout, NEW)
    stopped = []
    thread = release.watch_in_background(watch, lambda: stopped.append(1), interval=0.01)
    thread.join(timeout=10)
    assert stopped == [1]


def test_terminate_self_sends_sigterm(monkeypatch):
    sent = []
    monkeypatch.setattr(release.os, "kill", lambda *args: sent.append(args))
    release.terminate_self()
    assert sent == [(os.getpid(), signal.SIGTERM)]


def test_run_checked_raises_on_failure():
    with pytest.raises(subprocess.CalledProcessError):
        release.run_checked([sys.executable, "-c", "raise SystemExit(3)"])


def test_dependencies_install_once_per_lock(checkout, tmp_path):
    ran, stamp = [], tmp_path / "stamp"
    assert release.install_dependencies(ran.append, checkout, stamp)
    assert [argv[:2] for argv in ran] == [["uv", "export"], ["uv", "pip"]]
    assert not release.install_dependencies(ran.append, checkout, stamp)
    (checkout / "uv.lock").write_text("new", encoding="utf8")
    assert release.install_dependencies(ran.append, checkout, stamp)


def test_a_failed_install_is_tried_again(checkout, tmp_path):
    def fail(_argv):
        raise subprocess.CalledProcessError(1, "uv")

    stamp = tmp_path / "stamp"
    with pytest.raises(subprocess.CalledProcessError):
        release.install_dependencies(fail, checkout, stamp)
    assert not stamp.exists()


def test_prepare_migrates_after_installing(checkout, tmp_path):
    ran = []
    release.prepare(ran.append, checkout, tmp_path / "stamp")
    assert ran[-1][1:] == ["-m", "alembic", "upgrade", "head"]
    assert len(ran) == 3


def test_main_records_the_release_then_prepares_then_becomes_the_command(monkeypatch):
    steps = []
    monkeypatch.setenv(release.RELEASE_ENV, "")
    monkeypatch.setattr(release, "current", lambda: "sha:code")
    monkeypatch.setattr(release, "prepare", lambda: steps.append(os.environ[release.RELEASE_ENV]))
    monkeypatch.setattr(release.os, "execv", lambda _path, argv: steps.append(argv[1:]))
    release.main(["worker"])
    assert steps == ["sha:code", ["-m", "d2_trader", "worker"]]


def test_main_installs_for_the_image_build(monkeypatch):
    ran = []
    monkeypatch.setattr(release, "install_dependencies", lambda: ran.append(1))
    assert release.main(["deps"]) == 0
    assert ran == [1]


def test_main_refuses_anything_else():
    assert release.main(["nonsense"]) == 2


def test_main_waits_for_the_deploy(monkeypatch):
    monkeypatch.setattr(release, "wait_for_live", lambda: 7)
    assert release.main(["wait"]) == 7


def test_main_checkout_is_the_root_itself(checkout):
    assert release.main_checkout(checkout) == checkout


def test_main_checkout_of_a_worktree_is_the_checkout_it_was_cut_from(checkout, tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    gitdir = checkout / ".git/worktrees/tree"
    gitdir.mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf8")
    assert release.main_checkout(tree) == checkout.resolve()


def replies(*bodies):
    """A fetch answering each call with the next body; an exception body is raised."""
    queue = list(bodies)

    def get(_url):
        body = queue.pop(0)
        if isinstance(body, Exception):
            raise body
        return body

    return get


def test_the_time_module_is_the_clock_a_wait_reads():
    clock: release.Clock = time
    assert release.wait_for_live.__defaults__[-1] is clock


class Clock:
    """A `release.Clock` whose sleep only moves its own time forward."""

    def __init__(self):
        self.now = 0.0
        self.pauses = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.pauses.append(seconds)
        self.now += seconds


def waiting(checkout, get, clock, timeout=60.0):
    return release.wait_for_live(checkout, "http://dash", get, timeout, 5.0, clock)


def test_fetch_reads_a_url(tmp_path):
    page = tmp_path / "release.json"
    page.write_text('{"release": "s:c"}', encoding="utf8")
    assert release.fetch(page.as_uri()) == '{"release": "s:c"}'


def test_served_release_reads_the_endpoint():
    assert release.served_release("http://dash", replies('{"release": "s:c"}')) == "s:c"


def test_served_release_is_none_while_the_dashboard_is_down():
    assert release.served_release("http://dash", replies(OSError("refused"))) is None
    assert release.served_release("http://dash", replies("<html>")) is None


def test_wait_returns_once_the_dashboard_runs_the_checkouts_code(checkout, capsys):
    live = json.dumps({"release": f"{NEW}:{release.digest(checkout, release.CODE)}"})
    old = json.dumps({"release": f"{OLD}:before"})
    clock = Clock()
    get = replies(old, OSError("restarting"), live)
    assert waiting(checkout, get, clock) == 0
    assert clock.pauses == [5.0, 5.0]
    assert OLD[:10] in capsys.readouterr().out


def test_wait_gives_up_at_its_deadline(checkout, capsys):
    clock = Clock()
    get = replies(*[OSError("down")] * 20)
    assert waiting(checkout, get, clock, timeout=12.0) == 1
    assert "after 12s" in capsys.readouterr().out


def test_wait_says_when_nothing_deploys_the_dashboard(checkout, capsys):
    assert waiting(checkout, replies('{"release": ""}'), Clock()) == 2
    assert "not started by a release" in capsys.readouterr().out


def test_the_module_needs_nothing_installed():
    """It runs before a newly merged dependency is installed."""
    code = "import d2_trader.release"
    done = subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
        [sys.executable, "-S", "-c", code], cwd=release.ROOT, capture_output=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
