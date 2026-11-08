# TODO — D2 Trader

Freshly generated. These are the things the template deliberately left for you,
because guessing them wrong is worse than leaving them blank.

## Setup

- [ ] Fill in `.env` from `.env.example` (it is gitignored; nothing works without it)
- [ ] Confirm `python scripts/sync-devkit.py --list` shows a stamped `DEVKIT_VERSION`
- [ ] Set `DEVKIT_DIR` in CI so the drift check actually gates — a
      `--check` that prints "nothing to do (skipping)" is checking nothing
      (once `DEVKIT_VERSION` is stamped it fails instead of skipping; to check
      drift on a machine with no devkit clone, `pre-commit run devkit-drift`)
- [ ] Replace the placeholder DB password in `.env` (the committed one is a
      local-dev placeholder and is fine to keep local, but never reuse it remotely)

## Before relying on it

- [ ] Add the items to watch: Traderie item prices are only fetched for watched items
- [ ] Set `DISCORD_WEBHOOK_URL` or `NTFY_URL` to get alerts on your phone

## Ideas

- Price rolls: `listings.attrs` could keep a listing's stats, so a perfect copy is not
  averaged with a low roll
- Learn each item's usual launch premium and end-of-season discount from past seasons,
  once a season or two of data exists

## Archive

- [x] Decide about Traderie: turned on for personal use, 2026-11-07
- [x] Decide about d2jsp: not used, 2026-11-07. Traderie is the price source; the rune
      price paste and the forum gold rate stay available, unused

- [x] Replace the placeholder in `d2_trader/` with something that does the job
- [x] Delete `tests/test_smoke.py` once real tests exist
