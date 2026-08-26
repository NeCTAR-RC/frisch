"""The normalised record every collector emits, and service identity.

An :class:`Observation` says "source X saw service S at version V in
environment E, as of time T". Collectors are pure producers of observations;
:mod:`frisch.ingest` is the only thing that turns them into stored state.

Versions are opaque strings everywhere. Kolla tags like
``2024.1-eom-26-gc1dd7ae343-10-21`` are not semver: versions are only ever
compared for equality and ordered by time, never parsed.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
import re

from frisch.config import DEFAULT_CLUSTER, ServicesConfig

# version_kind
PINNED = "pinned"
FLOATING = "floating"  # targetRevision: HEAD, image tag "latest", ...

# precision
EXACT = "exact"  # git commit time
INTERVAL = "interval"  # observed between interval_start and changed_at

# component for the primary artefact of an instance (the empty string rather
# than NULL so it can participate in the DB unique constraint).
PRIMARY = ""


@dataclasses.dataclass(frozen=True)
class Observation:
    source: str  # "argocd" | "puppet" | "deb"
    env: str  # "test" | "prod" (open set)
    source_key: str  # stable per-source identity, e.g. "main/keystone"
    service: str  # canonical name after identity mapping
    version: str | None  # opaque; None = tombstone (removed from source)
    changed_at: datetime  # naive UTC
    ref: str  # idempotency ref: commit sha, or snapshot ref
    instance: str | None = None  # fan-out/cluster instance ("qld", "capi")
    component: str = PRIMARY  # sub-artefact ("crds", "image")
    version_kind: str = PINNED
    precision: str = EXACT
    interval_start: datetime | None = None
    meta: dict = dataclasses.field(default_factory=dict)


_PUPPET_KEY_RE = re.compile(
    r"(?:nectar::profile|profile::core)::(\w+)::.*image_tag"
)


class ServiceIdentity:
    """Maps raw per-source names onto canonical service names.

    Resolution order: explicit alias from config, then the per-source
    auto-default the collectors computed. Aliases are configured
    canonical-first (``aliases: {langstroth: {deb: [python3-langstroth]}}``)
    and reversed here.
    """

    def __init__(self, services: ServicesConfig):
        self._reverse: dict[tuple[str, str], str] = {}
        for canonical, per_source in (services.aliases or {}).items():
            for source, raw in (per_source or {}).items():
                names = raw if isinstance(raw, list) else [raw]
                for name in names:
                    self._reverse[(source, str(name))] = canonical

    def resolve(self, source: str, raw: str) -> str:
        return self._reverse.get((source, raw), raw)


def argocd_default_identity(
    stem: str,
    cluster: str,
    fanout_services: list[str],
    default_cluster: str = DEFAULT_CLUSTER,
) -> tuple[str, str | None]:
    """Service and instance for an Application file stem.

    Fan-out grouping is explicit config: ``aardvark-qld`` -> ("aardvark",
    "qld") only when "aardvark" is listed, so stems like ``dashboard-next``
    are never wrongly split. Apps on a non-default cluster get the cluster
    as their instance (``prometheus`` exists in both apps-main and
    apps-capi and must not collapse into one instance).
    """
    service, instance = stem, None
    for fanout in fanout_services:
        if stem == fanout:
            service, instance = fanout, None
            break
        if stem.startswith(fanout + "-"):
            service, instance = fanout, stem[len(fanout) + 1 :]
            break
    if instance is None and cluster != default_cluster:
        instance = cluster
    return service, instance


def puppet_default_identity(hiera_key: str) -> str | None:
    """Fallback service name from a hiera key, for keys with no renovate
    annotation (e.g. an image_tag renovate does not manage)."""
    m = _PUPPET_KEY_RE.search(hiera_key)
    return m.group(1) if m else None


def deb_default_identity(package: str) -> str:
    if package.startswith("python3-"):
        return package[len("python3-") :]
    return package
