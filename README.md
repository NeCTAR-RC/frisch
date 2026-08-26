# frisch

Deployment version visibility for the Nectar Research Cloud.

Nectar services are deployed three ways: ArgoCD applications on kubernetes,
docker containers on puppet-managed VMs, and debian packages on VMs and bare
metal, each in a test and a prod environment (test is upgraded first). frisch
collects the deployed version of every service in both environments, records
when each environment changed to that version, and serves a dashboard that
shows the version matrix and per-service history, making promotion lag from
test to prod visible at a glance.

## How it works

```
collectors -> Observation -> ingest (single writer) -> MariaDB -> API -> SPA
```

Three collectors, one normalised record:

- **argocd** (git-declared): walks the history of the per-environment
  ArgoCD apps repos, parsing each Application CRD's
  `spec.source.targetRevision` (and `spec.sources` for multi-source apps).
  Change dates are exact git commit times; the full history is backfillable.
- **puppet** (git-declared): walks the puppet site repos (one per
  environment) and the shared `hieradata` repo, scanning hiera
  `*::image_tag` keys and their
  `# renovate:` annotations. Also exact, backfillable.
- **deb** (live): queries the PuppetDB package inventory in each environment
  for configured package names/patterns. Package versions are not in git, so
  frisch's own records are the only durable history; change times are
  intervals ("changed between X and Y"), bounded by the collection interval.

Versions are opaque strings everywhere -- they are never compared or ordered,
only equality-checked and timestamped.

## Running

```
pip install -e '.[test]'
frisch db upgrade                       # create/upgrade the schema
frisch-collect --backfill               # first run: full git history
frisch-collect                          # incremental (the CronJob entry point)
frisch-web                              # serve the dashboard on :8080
frisch show matrix                      # the same table, in a terminal
```

Configuration is layered: `FRISCH_*` environment variables over a YAML config
file (`--config`, `FRISCH_CONFIG`, `~/.config/frisch/frisch.yaml` or
`/etc/frisch/frisch.yaml`) over defaults. See `etc/frisch.yaml.sample`.
For local development, point the repo urls at your existing checkouts and use
the default SQLite database.

The SPA lives in `frontend/` (vite + React); `npm run dev` proxies `/api` to a
locally running `frisch-web`. In the container image the built bundle is
served by the backend.

## Deployment prerequisites

- A database and user on the MariaDB cluster in each environment.
- A read-only deploy key for the five source repos.
- PuppetDB package inventory enabled (`package_inventory_enabled` on agents)
  and network/auth access to the PuppetDB endpoints; until then the deb
  source reports itself unavailable on the status page.

## Development

```
tox            # unit tests and lint
tox -e pep8    # lint only
tox -e frontend  # build the SPA (needs npm)
```

Releases use [reno](https://docs.openstack.org/reno/) for release notes.
Contributions go through gerrit; use conventional commits and `git commit -s`.
