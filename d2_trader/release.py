"""Deploys merged code: how a container starts, and how it knows to start again.

    python -m d2_trader.release serve|worker   # what the stack's containers run
    python -m d2_trader.release deps           # what the image build runs
    python -m d2_trader.release wait           # blocks until the dashboard serves main

`wait` is the one call a session makes to see a merge live, in place of a loop that
curls a page and sleeps: it returns once the dashboard's release (`/api/release`) runs
the code the main checkout holds, and fails after `WAIT_SECONDS`.

The containers mount the checkout, so a merge reaches their files, but a running process
keeps what it imported. Each one therefore watches for `main` moving and exits when the
code did too; `restart: unless-stopped` starts it again through `prepare`, which installs
changed dependencies and applies migrations first.

The trigger is the commit, not the files: an uncommitted edit or a feature branch in the
checkout deploys nothing. Git moves the branch after it has written the files, so they are
complete by then.

Standard library only: this runs before a newly added dependency is installed.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "refs/heads/main"
CODE = ("d2_trader", "migrations", "alembic.ini", "pyproject.toml", "uv.lock")
DEPENDENCIES = ("pyproject.toml", "uv.lock")
# In the container's own filesystem, beside the packages it describes.
STAMP = Path("/var/tmp/d2-trader-deps")  # noqa: S108 - a fixed path in a single-user container
REQUIREMENTS = "/var/tmp/d2-trader-requirements.txt"  # noqa: S108 - as above
# Set by `main` for the process it becomes. Unset, nothing watches: a host run, a test.
RELEASE_ENV = "D2_TRADER_RELEASE"
POLL_SECONDS = 30.0
# The watch's own interval, a restart and `prepare`'s migrations, with room to spare.
WAIT_SECONDS = 600.0
WAIT_INTERVAL = 5.0
DASHBOARD = "http://127.0.0.1:8010"

Run = Callable[[list[str]], object]
Fetch = Callable[[str], str]


class Clock(Protocol):
    """What `wait_for_live` reads time through: the `time` module, or a test's."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float, /) -> None: ...


def main_commit(root: Path = ROOT) -> str:
    """The commit `main` is at, or "" when the checkout is not on it.

    Reads git's files because the image has no git. A worktree's `.git` is a file, so a
    worktree's own stack never deploys itself.
    """
    git = root / ".git"
    try:
        if (git / "HEAD").read_text(encoding="utf8").strip() != f"ref: {BRANCH}":
            return ""
        loose = git / BRANCH
        if loose.is_file():
            return loose.read_text(encoding="utf8").strip()
        for line in (git / "packed-refs").read_text(encoding="utf8").splitlines():
            sha, _, name = line.partition(" ")
            if name == BRANCH:
                return sha
    except OSError:
        pass
    return ""


def digest(root: Path, names: tuple[str, ...]) -> str:
    """One hash over the files, and the folders' files, that `names` lists."""
    total = hashlib.sha256()
    for name in names:
        top = root / name
        files = sorted(top.rglob("*")) if top.is_dir() else [top]
        for path in files:
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            total.update(path.relative_to(root).as_posix().encode() + b"\0")
            total.update(hashlib.sha256(path.read_bytes()).digest())
    return total.hexdigest()


def current(root: Path = ROOT) -> str:
    # The commit first: a merge landing between the two reads is then seen as a new commit.
    commit = main_commit(root)
    return f"{commit}:{digest(root, CODE)}"


class Watch:
    """Says when `main` has moved to different code than this process started on."""

    def __init__(self, release: str, root: Path = ROOT) -> None:
        self.commit, _, self.code = release.partition(":")
        self.root = root

    def stale(self) -> bool:
        if not self.code:
            return False
        commit = main_commit(self.root)
        if not commit or commit == self.commit:
            return False
        try:
            code = digest(self.root, CODE)
        except OSError:
            return False  # a file went while it was read; the next look settles it
        if code != self.code:
            return True
        self.commit = commit  # a merge that touched no code
        return False


def started_release() -> Watch:
    return Watch(os.environ.get(RELEASE_ENV, ""))


def terminate_self() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def watch_in_background(
    watch: Watch,
    stop: Callable[[], None] = terminate_self,
    interval: float = POLL_SECONDS,
) -> threading.Thread | None:
    """Call `stop` once the release is stale. None when this process is not deployed."""
    if not watch.code:
        return None
    pause = threading.Event()

    def look() -> None:
        while not watch.stale():
            pause.wait(interval)
        stop()

    thread = threading.Thread(target=look, name="release-watch", daemon=True)
    thread.start()
    return thread


def run_checked(argv: list[str]) -> None:
    subprocess.run(argv, cwd=ROOT, check=True)  # noqa: S603 - fixed arguments, no shell


def install_dependencies(run: Run = run_checked, root: Path = ROOT, stamp: Path = STAMP) -> bool:
    """Install what `uv.lock` pins, unless this container already has it."""
    wanted = digest(root, DEPENDENCIES)
    if stamp.is_file() and stamp.read_text(encoding="utf8") == wanted:
        return False
    export = ["uv", "export", "--frozen", "--no-emit-project", "--extra", "dev", "--quiet"]
    run([*export, "--output-file", REQUIREMENTS])
    run(["uv", "pip", "install", "--system", "--no-cache", "-r", REQUIREMENTS])
    stamp.write_text(wanted, encoding="utf8")  # last, so a failed install is tried again
    return True


def prepare(run: Run = run_checked, root: Path = ROOT, stamp: Path = STAMP) -> None:
    install_dependencies(run, root, stamp)
    run([sys.executable, "-m", "alembic", "upgrade", "head"])


def main_checkout(root: Path = ROOT) -> Path:
    """The checkout the stack mounts: `root` itself, or the one a worktree at `root` is of.

    A worktree's `.git` file names `<main>/.git/worktrees/<name>`.
    """
    git = root / ".git"
    if not git.is_file():
        return root
    _, _, gitdir = git.read_text(encoding="utf8").strip().partition("gitdir:")
    path = Path(gitdir.strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve().parents[2]


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as reply:  # noqa: S310 - a fixed local URL
        return reply.read().decode("utf8")


def served_release(base: str = DASHBOARD, get: Fetch = fetch) -> str | None:
    """What the dashboard reports it runs: "" when nothing deployed it, None while down."""
    try:
        return str(json.loads(get(f"{base}/api/release")).get("release", ""))
    except (OSError, ValueError, AttributeError):
        return None  # restarting, or a dashboard from before this endpoint existed


def wait_for_live(
    root: Path = ROOT,
    base: str = DASHBOARD,
    get: Fetch = fetch,
    timeout: float = WAIT_SECONDS,
    interval: float = WAIT_INTERVAL,
    clock: Clock = time,
) -> int:
    """0 once the dashboard runs the main checkout's code; 1 on timeout; 2 if undeployed."""
    checkout = main_checkout(root)
    wanted = digest(checkout, CODE)
    deadline = clock.monotonic() + timeout
    while True:
        served = served_release(base, get)
        if served == "":
            print(f"{base} was not started by a release, so no merge deploys it")
            return 2
        if served is not None and served.partition(":")[2] == wanted:
            print(f"{base} serves {checkout}'s main at {main_commit(checkout)[:10]}")
            return 0
        if clock.monotonic() >= deadline:
            print(f"{base} still did not serve {checkout}'s main after {timeout:.0f}s")
            return 1
        clock.sleep(interval)


def main(argv: list[str]) -> int:
    if argv == ["deps"]:
        install_dependencies()
        return 0
    if argv == ["wait"]:
        return wait_for_live()
    if argv not in (["serve"], ["worker"]):
        print("usage: python -m d2_trader.release serve|worker|deps|wait", file=sys.stderr)
        return 2
    # Before `prepare`: a merge landing during it must still count as new.
    os.environ[RELEASE_ENV] = current()
    prepare()
    os.execv(  # noqa: S606 - fixed arguments, no shell wanted
        sys.executable, [sys.executable, "-m", "d2_trader", *argv]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
