"""Puppet hiera image_tag collector.

Two kinds of git source:

- the per-environment site repos, where every ``*::image_tag`` key in the
  watched nodegroup files is collected, with the ``# renovate:
  depName=... packageName=...`` comment on the preceding line supplying the
  service name and image path;
- the shared hieradata repo, where only the explicitly configured
  ``extra_keys`` are collected, with per-environment file lists in hiera
  precedence order (a value found past the first file is flagged
  ``inherited``).

The data files hold multi-kilobyte ``ENC[GPG,...]`` scalars and the renovate
annotation is a YAML comment, so blobs are line-scanned with a regex --
never ``yaml.safe_load``-ed.
"""

from __future__ import annotations

import logging
import re

from frisch.collectors import gitrepo
from frisch.collectors.base import Collector, CollectResult, CursorStore
from frisch.config import GitRepoConfig
from frisch import model

LOG = logging.getLogger(__name__)

_KEY_RE = re.compile(
    r"^(?P<key>[A-Za-z0-9_:]+::image_tag):\s*'?(?P<val>[^'\"\s#]+)['\"]?\s*$"
)
_ANNOTATION_RE = re.compile(
    r"^#\s*renovate:\s*depName=(?P<dep>\S+)(?:\s+packageName=(?P<pkg>\S+))?"
)


def scan_image_tags(blob: bytes) -> dict[str, dict]:
    """key -> {value, dep, pkg} for every image_tag key in a hiera blob."""
    found: dict[str, dict] = {}
    previous = ""
    for raw_line in blob.decode("utf-8", errors="replace").splitlines():
        line = raw_line.rstrip()
        m = _KEY_RE.match(line)
        if m:
            entry: dict = {"value": m.group("val"), "dep": None, "pkg": None}
            ann = _ANNOTATION_RE.match(previous.strip())
            if ann:
                entry["dep"] = ann.group("dep")
                entry["pkg"] = ann.group("pkg")
            found[m.group("key")] = entry
        previous = line
    return found


class PuppetCollector(Collector):
    name = "puppet"

    def environments(self) -> list[str]:
        envs = set(self.config.puppet.sites)
        if self.config.puppet.hieradata:
            envs.update(self.config.puppet.hieradata.env_files)
        return sorted(envs)

    def cursor_keys(self, env: str) -> list[str]:
        keys = []
        if env in self.config.puppet.sites:
            keys.append(f"puppet:site:{env}")
        hieradata = self.config.puppet.hieradata
        if hieradata and env in hieradata.env_files:
            keys.append(f"puppet:hieradata:{env}")
        return keys

    def _mirror(self, name: str, repo: GitRepoConfig) -> gitrepo.GitMirror:
        return gitrepo.GitMirror(
            self.config.git_cache_dir, name, repo.url, repo.branch
        )

    def _service_for(self, key: str, dep: str | None) -> str | None:
        hieradata = self.config.puppet.hieradata
        if hieradata and key in hieradata.extra_keys:
            return self.identity.resolve(self.name, hieradata.extra_keys[key])
        if dep:
            return self.identity.resolve(self.name, dep)
        fallback = model.puppet_default_identity(key)
        if fallback:
            return self.identity.resolve(self.name, fallback)
        return None

    def _effective(
        self,
        mirror: gitrepo.GitMirror,
        sha: str,
        files: list[str],
        keys: dict[str, str] | None,
    ) -> dict[str, dict]:
        """Effective key values at a commit: files are in precedence order,
        first file containing a key wins. ``keys`` restricts collection to
        the configured extra_keys (hieradata); None collects everything."""
        effective: dict[str, dict] = {}
        for index, path in enumerate(files):
            blob = mirror.show(sha, path)
            if blob is None:
                continue
            for key, entry in scan_image_tags(blob).items():
                if keys is not None and key not in keys:
                    continue
                if key in effective:
                    continue
                entry["file"] = path
                entry["inherited"] = index > 0
                effective[key] = entry
        return effective

    def _walk(
        self,
        env: str,
        mirror: gitrepo.GitMirror,
        cursor_key: str,
        cursors: CursorStore,
        files: list[str],
        keys: dict[str, str] | None,
        origin: str,
    ) -> tuple[list[model.Observation], dict]:
        mirror.sync()
        cursor = mirror.resolve_cursor(cursors.get(cursor_key))
        tip = mirror.tip()

        state: dict[str, dict] = (
            self._effective(mirror, cursor, files, keys) if cursor else {}
        )
        observations: list[model.Observation] = []
        commits = mirror.log(cursor, files)
        for commit in commits:
            effective = self._effective(mirror, commit.sha, files, keys)
            for key, entry in effective.items():
                service = self._service_for(key, entry["dep"])
                if service is None:
                    LOG.debug("no service mapping for hiera key %s", key)
                    continue
                meta = {
                    "commit": commit.sha,
                    "author": commit.author,
                    "subject": commit.subject,
                    "hiera_key": key,
                    "file": entry["file"],
                    "repo": origin,
                }
                if entry["pkg"]:
                    meta["package_name"] = entry["pkg"]
                if entry["inherited"]:
                    meta["inherited"] = True
                observations.append(
                    model.Observation(
                        source=self.name,
                        env=env,
                        source_key=key,
                        service=service,
                        version=entry["value"],
                        changed_at=commit.when,
                        precision=model.EXACT,
                        ref=commit.sha,
                        meta=meta,
                    )
                )
            for key in set(state) - set(effective):
                service = self._service_for(key, state[key].get("dep"))
                if service is None:
                    continue
                observations.append(
                    model.Observation(
                        source=self.name,
                        env=env,
                        source_key=key,
                        service=service,
                        version=None,
                        changed_at=commit.when,
                        precision=model.EXACT,
                        ref=commit.sha,
                        meta={
                            "commit": commit.sha,
                            "subject": commit.subject,
                            "repo": origin,
                        },
                    )
                )
            state = effective

        cursors.set(cursor_key, tip)
        return observations, {"commits": len(commits), "tip": tip}

    def collect(self, env: str, cursors: CursorStore) -> CollectResult:
        observations: list[model.Observation] = []
        stats: dict = {}

        site = self.config.puppet.sites.get(env)
        if site:
            mirror = self._mirror(f"puppet-site-{env}", site.repo)
            obs, site_stats = self._walk(
                env,
                mirror,
                f"puppet:site:{env}",
                cursors,
                site.files,
                None,
                origin=f"site-{env}",
            )
            observations.extend(obs)
            stats["site"] = site_stats

        hieradata = self.config.puppet.hieradata
        if hieradata and env in hieradata.env_files:
            mirror = self._mirror("hieradata", hieradata.repo)
            obs, hd_stats = self._walk(
                env,
                mirror,
                f"puppet:hieradata:{env}",
                cursors,
                hieradata.env_files[env],
                hieradata.extra_keys,
                origin="hieradata",
            )
            observations.extend(obs)
            stats["hieradata"] = hd_stats

        return CollectResult(observations=observations, stats=stats)
