"""ArgoCD Application collector.

Walks the history of the argocd-apps repo for an environment. Each
Application file's ``spec.source.targetRevision`` (or the entries of
``spec.sources``) is the deployed version. One primary observation per app,
plus one per extra source (``component``), plus opt-in observations from
helm values paths where an image tag is the meaningful version.

``source_key`` is ``<cluster>/<file stem>`` (not the raw path) so the
historic ``apps/`` -> ``apps-main/`` rename keeps one continuous history.
"""

from __future__ import annotations

import logging
import posixpath

import yaml

from frisch.collectors import gitrepo
from frisch.collectors.base import Collector, CollectResult, CursorStore
from frisch.errors import ConfigError
from frisch import model

LOG = logging.getLogger(__name__)

FLOATING_REVISIONS = {"HEAD", "head", "latest", ""}


def _is_floating(value: str) -> bool:
    return value in FLOATING_REVISIONS


class ParsedSource:
    def __init__(
        self,
        name: str,
        version: str,
        floating: bool,
        chart: str | None,
        repo_url: str | None,
    ):
        self.name = name
        self.version = version
        self.floating = floating
        self.chart = chart
        self.repo_url = repo_url


def _dig(data, dotted_path: str):
    node = data
    for part in dotted_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def parse_app(
    blob: bytes, stem: str, overrides: dict
) -> list[tuple[str, ParsedSource]]:
    """Parse an Application blob into [(component, source)] with the
    primary source first under component '' (PRIMARY)."""
    doc = yaml.safe_load(blob)
    if not isinstance(doc, dict):
        return []
    spec = doc.get("spec") or {}
    raw_sources = spec.get("sources")
    if not raw_sources:
        single = spec.get("source")
        raw_sources = [single] if single else []

    parsed: list[ParsedSource] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        revision = str(raw.get("targetRevision") or "")
        chart = raw.get("chart")
        repo_url = raw.get("repoURL")
        if chart:
            name = str(chart)
        elif raw.get("path"):
            name = posixpath.basename(str(raw["path"]).rstrip("/")) or "git"
        else:
            name = (
                posixpath.basename(
                    str(repo_url or "").rstrip("/")
                ).removesuffix(".git")
                or "git"
            )
        parsed.append(
            ParsedSource(
                name=name,
                version=revision or "HEAD",
                floating=_is_floating(revision),
                chart=str(chart) if chart else None,
                repo_url=str(repo_url) if repo_url else None,
            )
        )
    if not parsed:
        return []

    primary = None
    override = overrides.get("primary_source")
    if override:
        kind, _, wanted = str(override).partition(":")
        if kind != "chart":
            raise ConfigError(
                f"app_overrides.{stem}.primary_source: only 'chart:<name>' "
                f"is supported, got {override!r}"
            )
        primary = next((s for s in parsed if s.chart == wanted), None)
    if primary is None:
        primary = next((s for s in parsed if s.chart == stem), None)
    if primary is None:
        primary = next((s for s in parsed if s.chart), None)
    if primary is None:
        primary = parsed[0]

    result = [(model.PRIMARY, primary)]
    used_names: set[str] = set()
    for source in parsed:
        if source is primary:
            continue
        component = source.name
        while component in used_names or component == model.PRIMARY:
            component += "+"
        used_names.add(component)
        result.append((component, source))

    # Opt-in helm values versions (e.g. event-exporter's image.tag is the
    # meaningful version while the chart pin barely moves).
    for dotted in overrides.get("values_version_paths", []):
        for raw in raw_sources:
            values_text = _dig(raw, "helm.values")
            if not values_text:
                continue
            try:
                values = yaml.safe_load(values_text)
            except yaml.YAMLError:
                continue
            value = _dig(values, dotted)
            if value is None:
                continue
            component = dotted.rsplit(".", 1)[0].replace(".", "/")
            result.append(
                (
                    component,
                    ParsedSource(
                        name=component,
                        version=str(value),
                        floating=_is_floating(str(value)),
                        chart=None,
                        repo_url=None,
                    ),
                )
            )
            break
    return result


class ArgocdCollector(Collector):
    name = "argocd"

    def environments(self) -> list[str]:
        return list(self.config.argocd.repos)

    def cursor_keys(self, env: str) -> list[str]:
        return [f"argocd:{env}"]

    def _mirror(self, env: str) -> gitrepo.GitMirror:
        repo = self.config.argocd.repos[env]
        return gitrepo.GitMirror(
            self.config.git_cache_dir,
            f"argocd-{env}",
            repo.url,
            repo.branch,
        )

    def _locate(self, path: str) -> tuple[str, str] | None:
        """Map a repo path to (cluster, stem); None when unwatched."""
        cfg = self.config.argocd
        directory, _, filename = path.partition("/")
        if directory not in cfg.watched_dirs or "/" in filename:
            return None
        if not filename.endswith((".yaml", ".yml")):
            return None
        cluster = cfg.cluster_by_dir.get(directory, directory)
        return cluster, filename.rsplit(".", 1)[0]

    def _identity(self, stem: str, cluster: str) -> tuple[str, str | None]:
        service, instance = model.argocd_default_identity(
            stem,
            cluster,
            self.config.argocd.fanout_services,
            self.config.argocd.default_cluster,
        )
        return self.identity.resolve(self.name, service), instance

    def _observations_for_file(
        self,
        env: str,
        commit: gitrepo.CommitInfo,
        path: str,
        blob: bytes,
    ) -> list[model.Observation]:
        located = self._locate(path)
        if located is None:
            return []
        cluster, stem = located
        overrides = self.config.argocd.app_overrides.get(stem, {})
        try:
            components = parse_app(blob, stem, overrides)
        except yaml.YAMLError as exc:
            LOG.warning(
                "unparsable app %s at %s: %s", path, commit.sha[:10], exc
            )
            return []
        service, instance = self._identity(stem, cluster)
        source_key = f"{cluster}/{stem}"
        observations = []
        for component, src in components:
            observations.append(
                model.Observation(
                    source=self.name,
                    env=env,
                    source_key=source_key,
                    service=service,
                    instance=instance,
                    component=component,
                    version=src.version,
                    version_kind=(
                        model.FLOATING if src.floating else model.PINNED
                    ),
                    changed_at=commit.when,
                    precision=model.EXACT,
                    ref=commit.sha,
                    meta={
                        "commit": commit.sha,
                        "author": commit.author,
                        "subject": commit.subject,
                        "path": path,
                        **({"chart": src.chart} if src.chart else {}),
                        **({"repo_url": src.repo_url} if src.repo_url else {}),
                    },
                )
            )
        return observations

    def _tombstones(
        self,
        env: str,
        commit: gitrepo.CommitInfo,
        source_key: str,
        components: set[str],
    ) -> list[model.Observation]:
        cluster, stem = source_key.split("/", 1)
        service, instance = self._identity(stem, cluster)
        return [
            model.Observation(
                source=self.name,
                env=env,
                source_key=source_key,
                service=service,
                instance=instance,
                component=component,
                version=None,
                changed_at=commit.when,
                precision=model.EXACT,
                ref=commit.sha,
                meta={
                    "commit": commit.sha,
                    "author": commit.author,
                    "subject": commit.subject,
                },
            )
            for component in sorted(components)
        ]

    def _state_at(
        self, mirror: gitrepo.GitMirror, sha: str
    ) -> dict[str, set[str]]:
        """(source_key -> components) as of a commit, to seed tombstone
        detection for an incremental walk."""
        state: dict[str, set[str]] = {}
        for path in mirror.ls_files(sha, self.config.argocd.watched_dirs):
            located = self._locate(path)
            if located is None:
                continue
            cluster, stem = located
            blob = mirror.show(sha, path)
            if blob is None:
                continue
            overrides = self.config.argocd.app_overrides.get(stem, {})
            try:
                components = parse_app(blob, stem, overrides)
            except yaml.YAMLError:
                continue
            state.setdefault(f"{cluster}/{stem}", set()).update(
                component for component, _ in components
            )
        return state

    def collect(self, env: str, cursors: CursorStore) -> CollectResult:
        mirror = self._mirror(env)
        mirror.sync()
        cursor_key = self.cursor_keys(env)[0]
        cursor = mirror.resolve_cursor(cursors.get(cursor_key))
        tip = mirror.tip()

        state = self._state_at(mirror, cursor) if cursor else {}
        observations: list[model.Observation] = []
        parse_errors = 0
        commits = mirror.log(cursor, self.config.argocd.watched_dirs)
        for commit in commits:
            for change in commit.changes:
                if change.status == "D":
                    located = self._locate(change.path)
                    if located is None:
                        continue
                    source_key = "/".join(located)
                    gone = state.pop(source_key, set())
                    observations.extend(
                        self._tombstones(env, commit, source_key, gone)
                    )
                    continue

                located = self._locate(change.path)
                if located is None:
                    continue
                source_key = "/".join(located)

                # A rename that changes the source_key ends the old line.
                if change.old_path:
                    old = self._locate(change.old_path)
                    if old is not None and "/".join(old) != source_key:
                        gone = state.pop("/".join(old), set())
                        observations.extend(
                            self._tombstones(env, commit, "/".join(old), gone)
                        )

                blob = mirror.show(commit.sha, change.path)
                if blob is None:
                    parse_errors += 1
                    continue
                obs = self._observations_for_file(
                    env, commit, change.path, blob
                )
                if not obs and change.status in ("A", "M", "R", "C"):
                    parse_errors += 1
                new_components = {o.component for o in obs}
                gone = state.get(source_key, set()) - new_components
                observations.extend(obs)
                observations.extend(
                    self._tombstones(env, commit, source_key, gone)
                )
                state[source_key] = new_components

        cursors.set(cursor_key, tip)
        return CollectResult(
            observations=observations,
            stats={
                "commits": len(commits),
                "parse_errors": parse_errors,
                "tip": tip,
            },
        )
