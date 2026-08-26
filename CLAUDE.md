# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`frisch` tracks the deployed version of every Nectar service in **test** and
**prod** and when each environment changed to that version. Data comes from
three sources: ArgoCD apps repos (git), puppet site repos + shared hieradata
(git), and the PuppetDB package inventory (live). The full design rationale
lives in the plan at the repo's inception; the short form is in README.md.

## Commands

```bash
tox                       # default envlist: py314 (pytest + coverage) and pep8
tox -e pep8               # lint only
.venv/bin/pytest tests/test_ingest.py::test_change_appends_event   # single test
pip install -e '.[test]'  # editable install (needed to refresh CLI entry points)
frisch db upgrade         # alembic upgrade head against the configured DB
frisch-collect --backfill --config etc/dev.yaml   # full history rebuild
```

- `tox -e releasenotes` (reno) needs at least one git commit; not in the
  default envlist.
- `tox -e frontend` builds the SPA (needs npm); Python tests never require it.

## Architecture

The pipeline is `collectors -> Observation -> ingest -> DB -> queries -> API/SPA`.

- **`model.py`** holds the `Observation` contract and service-identity
  resolution. Collectors are pure producers: they emit `Observation`s and
  never touch the DB. **`ingest.py`** is the single writer; everything else
  reads via `db/queries.py`.
- Versions are **opaque strings**: never parse, compare or order them
  (kolla tags like `2024.1-eom-26-g...` are not semver). Ordering is always
  by time.
- Git collectors share `collectors/gitrepo.py` (bare mirror clones, cursor
  per repo in the `git_cursor` table, `log --first-parent -M -C
  --name-status` walk, `git show` per blob). Idempotency: `version_event`
  is unique on `(instance_id, ref)` where ref = commit sha, so re-walking
  history (or a cursor reset after a force-push) is safe.
- The deb collector has no git history: `precision="interval"` events with
  `interval_start`, and the UI must say "changed between X and Y", never a
  fake exact time. A failed/unavailable source aborts that env's run and
  keeps prior state -- `frisch-collect` exits non-zero so the CronJob goes
  red rather than silently serving stale data.
- The puppet hiera files contain multi-KB `ENC[GPG,...]` blobs and the
  renovate annotation is a YAML *comment*: the puppet collector line-scans,
  it must NOT yaml.safe_load site data files.
- DB is MariaDB in deployments, SQLite in dev/tests (same SQLAlchemy URL
  mechanism) -- keep column types portable and timestamps naive UTC.

## Conventions

Gerrit review (no branches, no PRs), conventional commits with `-s`, reno
release notes, ruff line-length 79, tox for tests. The reference sibling
project for patterns is `nectar-conformance`.
