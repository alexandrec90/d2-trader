# D2 Trader

Tracks D2R item prices per market, with watchlists and threshold alerts, built around
ladder resets. The README explains the sources and the reset model; read its "Data
sources" section before touching a collector.

## Tech Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Database | PostgreSQL |
| ORM / Migrations | SQLAlchemy 2 (async) + Alembic |
| Container | Docker + Docker Compose |
| Tests | pytest |
| Lint | ruff |

## Environment Variables

See `.env.example` for every variable. `.env` is gitignored and holds this
checkout's ports and credentials.

## Tooling

> Everything in this section needs the local Docker Desktop daemon. If it isn't
> running, make the code change and defer container/stack verification until it is
> (or to CI). Run `docker ps` first — an `npipe`/daemon error means Desktop is stopped.

### Scripts and the vendored harness

Both are covered by **`.claude/rules/engineering.md`** — script conventions (pure
importable functions, stdlib-only hooks, tests in the same change), the failure-artifact
rule, and how the `.devkit.toml` seam works. That file is vendored from
[devkit](https://github.com/alexandrec90/devkit) and drift-gated, so it is the
authority; this file does not repeat it.

### VS Code tasks

- A task lives in the workspace file and dispatches through devkit's
  `devkit_project.py`, never in `.vscode/tasks.json`; that file's comment says why.
- A script a task runs starts as `python <script>` under the machine's `python`, not
  this project's `.venv`, with no console. It imports only the standard library until
  it has handed itself to the `.venv`, as `scripts/run-tests.py` does, and it cannot
  rely on Ctrl+C: a task that serves until stopped has to watch for its parent going.
- Use `"type": "process"` so VS Code monitors the process directly — that is what
  makes the spinner stop and the exit-code icon appear reliably.
- Set `"close": false` in `presentation` so the terminal stays open for review.
- **Wrap with `notify-wrap.py`** for the completion toast; never call `notify.py`
  from inside a script. Notifications are a task-layer concern only.
- Label convention: `"Domain: Title Case Action"`, and **every task carries a
  `detail`** — that is the second line in the quick-pick, and the only place a
  one-click action can state its cost or blast radius.

### Failure artifacts (fix from a file, not from the terminal)

Any task or script whose failures an agent is expected to act on must persist the
failure to a **parseable artifact file** under `logs/`. Never rely on streamed
terminal output — it scrolls away and buries the signal. Keep the terminal to a
status line plus the artifact path; put everything needed to diagnose in the file.
Write the artifact on failure too, not just success, and overwrite per run.

### Docker subprocess calls

- **`docker compose exec` must use `-T`** — without it a pseudo-TTY is allocated and
  the subprocess handle can outlive the command, leaving the caller hung.

## Testing

**`.claude/rules/engineering.md`** is the authority: tests ship in the same commit,
every testable unit of logic is covered, regression test first, targeted runs locally
and full runs in CI.

Add this project's specifics *below* — fixtures, isolation rules, markers, what to mock
and where — but do not restate the policy above. It is vendored and drift-gated; a copy
here is a fork that will disagree with it the first time either is edited.

- **Database tests run on real Postgres**, because `listings` depends on `ON CONFLICT`
  and `NULLS NOT DISTINCT`. `conftest.py` applies migrations once per run, and each test
  runs inside a rolled-back transaction. Start one with `docker compose up -d db`.
- **Mock sites with `httpx.MockTransport`**, passed to the collector as `transport`.
- **The `local` marker reaches live sites** and is deselected by `addopts`; only
  `scripts/e2e.py` selects it, nightly, to catch a site changing its markup.
- **No test or tier may reach Traderie or d2jsp.** The collector reaches Traderie because
  the operator chose that; an unattended test tier is not the collector. Traderie's tests
  use saved response shapes.
- Windows needs the selector event loop for async psycopg. `conftest.py` sets it for
  tests and `run_async` in `__main__.py` sets it for the CLI.

## Guardrails

Baseline guardrails — including the instruction-file feedback loop (**never silently
work around a bad instruction**) — are in `.claude/rules/engineering.md`. Rules for
writing skills and rules themselves are in `.claude/rules/authoring.md`. Cross-reference
this project's own scoped rules here, one line each.

- **Every outbound request goes through `d2_trader/sources/http.py`.** Its random pacing
  spreads load. Never add a browser user agent, a proxy, a retry on a refusal or a
  challenge solver. A refusal is an answer.
- **`traderie_enabled` defaults to false in code**; the operator turns it on in `.env`, and
  did, for personal use (README, "Data sources"). d2jsp is never fetched: its rules forbid
  automation and it sits behind a bot challenge. Neither is a limit to work around.
- **A ladder price never crosses a season boundary.** Any new read of `listings` goes
  through `market_view` or states why it does not (`history` does, because the chart
  draws the boundaries).
