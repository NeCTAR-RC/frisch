"""Debian package collector, via the PuppetDB package inventory.

Package versions are not in git (puppet manages them with ``ensure =>
installed``), so this is a live snapshot source: each run queries the
environment's PuppetDB for the configured package names/patterns and rolls
the per-node rows up per package. frisch's own records are the only durable
history, and change times are honest intervals ("changed between the
previous run and this one"), never fake exact timestamps.

Prerequisite: package inventory collection must be enabled on the agents
(``package_inventory_enabled``); a probe marks the source unavailable --
surfaced on the status page -- rather than silently reporting nothing.
There is no fallback via the resources entity: a ``Package[x] { ensure =>
installed }`` resource carries no version.
"""

from __future__ import annotations

import hashlib
import json
import logging
import ssl
from datetime import datetime, UTC

import httpx

from frisch.collectors.base import Collector, CollectResult, CursorStore
from frisch.config import PuppetDBEnvConfig
from frisch.errors import SourceUnavailable
from frisch import model

LOG = logging.getLogger(__name__)

_NODES_SAMPLE = 5


def _q(value: str) -> str:
    """Quote a string for embedding in a PQL query."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _host_groups_by_certname(
    host_groups: dict[str, list[str]],
) -> dict[str, str]:
    return {
        certname: group
        for group, certnames in host_groups.items()
        for certname in certnames
    }


def _ssl_context(cfg: PuppetDBEnvConfig) -> ssl.SSLContext:
    """TLS context for PuppetDB, built by hand rather than from httpx's
    verify/cert shorthand: puppet CA chains in the wild fail the strict
    RFC 5280 profile checks Python 3.13+ enables by default (e.g.
    basicConstraints not marked critical on the CA), so keep chain and
    hostname verification but drop the profile checks. Partial chains stay
    trusted: ca.pem may hold the signing intermediate while the server
    sends only its leaf.
    """
    ctx = ssl.create_default_context(cafile=cfg.ca_cert)
    if cfg.verify:
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if cfg.client_cert:
        ctx.load_cert_chain(cfg.client_cert, cfg.client_key)
    return ctx


class PuppetDBClient:
    def __init__(self, cfg: PuppetDBEnvConfig):
        headers = {}
        token = cfg.resolved_token()
        if token:
            headers["X-Authentication"] = token
        self._client = httpx.Client(
            base_url=cfg.base_url,
            headers=headers,
            verify=_ssl_context(cfg),
            timeout=cfg.timeout,
        )

    def pql(self, query: str) -> list:
        try:
            resp = self._client.post("/pdb/query/v4", json={"query": query})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"PuppetDB query failed: {exc}") from exc

    def close(self) -> None:
        self._client.close()


class DebCollector(Collector):
    name = "deb"

    def environments(self) -> list[str]:
        return list(self.config.puppetdb.environments)

    def _package_filter(self) -> str:
        cfg = self.config.puppetdb
        clauses = [
            f"package_name ~ {_q(pattern)}" for pattern in cfg.package_patterns
        ]
        clauses.extend(
            f"package_name = {_q(name)}" for name in cfg.package_names
        )
        return " or ".join(clauses)

    def collect(self, env: str, cursors: CursorStore) -> CollectResult:
        package_filter = self._package_filter()
        if not package_filter:
            raise SourceUnavailable(
                "no package_patterns or package_names configured"
            )
        client = PuppetDBClient(self.config.puppetdb.environments[env])
        try:
            probe = client.pql("package_inventory[certname] { limit 1 }")
            if not probe:
                raise SourceUnavailable(
                    "package_inventory returned no rows -- is package "
                    "inventory collection enabled on the agents?"
                )
            rows = client.pql(
                "package_inventory[certname, package_name, version] "
                f"{{ {package_filter} }}"
            )
        finally:
            client.close()

        run_at = datetime.now(UTC).replace(tzinfo=None)
        host_group = _host_groups_by_certname(self.config.puppetdb.host_groups)
        # (package_name, group) -> version -> certnames. group is None for
        # hosts not covered by any configured host_groups entry -- they
        # keep the pre-grouping behaviour of one collapsed row per package.
        buckets: dict[tuple[str, str | None], dict[str, set[str]]] = {}
        for row in rows:
            key = (row["package_name"], host_group.get(row["certname"]))
            buckets.setdefault(key, {}).setdefault(row["version"], set()).add(
                row["certname"]
            )

        snapshot_ref = f"snap:{run_at.isoformat()}"
        observations = []
        packages_seen = {package_name for package_name, _ in buckets}
        for package_name, group in sorted(
            buckets, key=lambda k: (k[0], k[1] or "")
        ):
            by_version = buckets[(package_name, group)]
            groups = sorted(
                (
                    {
                        "version": version,
                        "node_count": len(nodes),
                        "nodes": sorted(nodes)[:_NODES_SAMPLE],
                    }
                    for version, nodes in by_version.items()
                ),
                key=lambda g: (-g["node_count"], g["version"]),
            )
            rollup = hashlib.sha1(
                json.dumps(groups, sort_keys=True).encode()
            ).hexdigest()[:12]
            all_nodes = sorted(
                node for nodes in by_version.values() for node in nodes
            )
            service = self.identity.resolve(
                self.name, model.deb_default_identity(package_name)
            )
            source_key = f"pkg:{package_name}"
            if group is not None:
                source_key = f"{source_key}@{group}"
            observations.append(
                model.Observation(
                    source=self.name,
                    env=env,
                    source_key=source_key,
                    service=service,
                    version=groups[0]["version"],
                    changed_at=run_at,
                    instance=group,
                    precision=model.INTERVAL,
                    ref=f"{run_at.isoformat()}:{rollup}",
                    meta={
                        "package": package_name,
                        "versions": groups,
                        "mixed": len(groups) > 1,
                        "node_count": len(all_nodes),
                    },
                )
            )
        return CollectResult(
            observations=observations,
            full_snapshot=True,
            snapshot_ref=snapshot_ref,
            stats={"rows": len(rows), "packages": len(packages_seen)},
        )
