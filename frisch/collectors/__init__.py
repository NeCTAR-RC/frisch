"""Collector registry."""

from __future__ import annotations

from frisch.collectors.argocd import ArgocdCollector
from frisch.collectors.base import Collector
from frisch.collectors.debs import DebCollector
from frisch.collectors.puppet import PuppetCollector
from frisch.config import Config
from frisch.model import ServiceIdentity

COLLECTORS = {
    ArgocdCollector.name: ArgocdCollector,
    PuppetCollector.name: PuppetCollector,
    DebCollector.name: DebCollector,
}


def build_collectors(
    config: Config, sources: list[str] | None = None
) -> list[Collector]:
    identity = ServiceIdentity(config.services)
    names = sources or list(COLLECTORS)
    return [COLLECTORS[name](config, identity) for name in names]
