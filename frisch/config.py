"""Layered configuration: environment (FRISCH_*) > config file > defaults.

Secrets (PuppetDB tokens, database passwords) are never inlined in the config
file, which may live in a git repo: they come from the environment or a
referenced ``*_file``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from frisch.errors import ConfigError

ENV_PREFIX = "FRISCH_"
DEFAULT_CONFIG_PATHS = (
    Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    / "frisch"
    / "frisch.yaml",
    Path("/etc/frisch/frisch.yaml"),
)

DEFAULT_WATCHED_DIRS = ["apps-main", "apps-capi", "apps"]
DEFAULT_CLUSTER_BY_DIR = {
    "apps-main": "main",
    "apps-capi": "capi",
    "apps": "main",
}
# Apps on the default cluster get no instance suffix; apps on any other
# cluster get the cluster name as their instance.
DEFAULT_CLUSTER = "main"


@dataclass
class GitRepoConfig:
    url: str
    branch: str = "master"


@dataclass
class ArgocdConfig:
    repos: dict[str, GitRepoConfig] = field(default_factory=dict)
    watched_dirs: list[str] = field(
        default_factory=lambda: list(DEFAULT_WATCHED_DIRS)
    )
    cluster_by_dir: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_CLUSTER_BY_DIR)
    )
    default_cluster: str = DEFAULT_CLUSTER
    fanout_services: list[str] = field(default_factory=list)
    # per file-stem: {"values_version_paths": [...], "primary_source": "chart:x"}
    app_overrides: dict[str, dict] = field(default_factory=dict)


@dataclass
class PuppetSiteConfig:
    repo: GitRepoConfig
    files: list[str] = field(default_factory=list)


@dataclass
class HieradataConfig:
    repo: GitRepoConfig
    # env -> ordered file list, hiera precedence order (first match wins)
    env_files: dict[str, list[str]] = field(default_factory=dict)
    # hiera key -> service name; only these keys are collected from hieradata
    extra_keys: dict[str, str] = field(default_factory=dict)


@dataclass
class PuppetConfig:
    sites: dict[str, PuppetSiteConfig] = field(default_factory=dict)
    hieradata: HieradataConfig | None = None


@dataclass
class PuppetDBEnvConfig:
    base_url: str
    token: str | None = None
    token_file: str | None = None
    ca_cert: str | None = None
    client_cert: str | None = None
    client_key: str | None = None
    verify: bool = True
    timeout: float = 60.0

    def resolved_token(self) -> str | None:
        if self.token:
            return self.token
        if self.token_file:
            return Path(self.token_file).read_text().strip()
        return None


@dataclass
class PuppetDBConfig:
    environments: dict[str, PuppetDBEnvConfig] = field(default_factory=dict)
    package_patterns: list[str] = field(default_factory=list)
    package_names: list[str] = field(default_factory=list)
    # group name -> certnames; hosts not listed keep the pre-grouping
    # behaviour of one collapsed row per package.
    host_groups: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ServicesConfig:
    # canonical name -> {source: raw-name-or-list}
    aliases: dict[str, dict] = field(default_factory=dict)
    display_names: dict[str, str] = field(default_factory=dict)
    hidden: list[str] = field(default_factory=list)


@dataclass
class Config:
    database_url: str = "sqlite:///.dev/frisch.db"
    git_cache_dir: Path = Path(".dev/git")
    environments: list[str] = field(default_factory=lambda: ["test", "prod"])
    web_static: Path | None = None
    argocd: ArgocdConfig = field(default_factory=ArgocdConfig)
    puppet: PuppetConfig = field(default_factory=PuppetConfig)
    puppetdb: PuppetDBConfig = field(default_factory=PuppetDBConfig)
    services: ServicesConfig = field(default_factory=ServicesConfig)


def _repo(raw: dict, context: str) -> GitRepoConfig:
    if not isinstance(raw, dict) or "url" not in raw:
        raise ConfigError(f"{context}: expected a mapping with a 'url' key")
    return GitRepoConfig(
        url=str(raw["url"]), branch=str(raw.get("branch", "master"))
    )


def _load_argocd(raw: dict) -> ArgocdConfig:
    cfg = ArgocdConfig()
    for env, repo in (raw.get("repos") or {}).items():
        cfg.repos[env] = _repo(repo, f"collectors.argocd.repos.{env}")
    if "watched_dirs" in raw:
        cfg.watched_dirs = [str(d) for d in raw["watched_dirs"]]
    if "cluster_by_dir" in raw:
        cfg.cluster_by_dir = {
            str(k): str(v) for k, v in raw["cluster_by_dir"].items()
        }
    if "default_cluster" in raw:
        cfg.default_cluster = str(raw["default_cluster"])
    cfg.fanout_services = [str(s) for s in raw.get("fanout_services", [])]
    cfg.app_overrides = dict(raw.get("app_overrides") or {})
    return cfg


def _load_puppet(raw: dict) -> PuppetConfig:
    cfg = PuppetConfig()
    for env, site in (raw.get("sites") or {}).items():
        cfg.sites[env] = PuppetSiteConfig(
            repo=_repo(site, f"collectors.puppet.sites.{env}"),
            files=[str(f) for f in site.get("files", [])],
        )
    if raw.get("hieradata"):
        h = raw["hieradata"]
        cfg.hieradata = HieradataConfig(
            repo=_repo(h, "collectors.puppet.hieradata"),
            env_files={
                env: [str(f) for f in files]
                for env, files in (h.get("env_files") or {}).items()
            },
            extra_keys={
                str(k): str(v) for k, v in (h.get("extra_keys") or {}).items()
            },
        )
    return cfg


def _load_puppetdb(raw: dict) -> PuppetDBConfig:
    cfg = PuppetDBConfig()
    for env, pdb in (raw.get("environments") or {}).items():
        if not isinstance(pdb, dict) or "base_url" not in pdb:
            raise ConfigError(
                f"collectors.puppetdb.environments.{env}: needs a base_url"
            )
        cfg.environments[env] = PuppetDBEnvConfig(
            base_url=str(pdb["base_url"]),
            token=pdb.get("token"),
            token_file=pdb.get("token_file"),
            ca_cert=pdb.get("ca_cert"),
            client_cert=pdb.get("client_cert"),
            client_key=pdb.get("client_key"),
            verify=bool(pdb.get("verify", True)),
            timeout=float(pdb.get("timeout", 60.0)),
        )
        env_token = os.environ.get(
            ENV_PREFIX + "PUPPETDB_TOKEN_" + env.upper()
        )
        if env_token:
            cfg.environments[env].token = env_token
    cfg.package_patterns = [str(p) for p in raw.get("package_patterns", [])]
    cfg.package_names = [str(n) for n in raw.get("package_names", [])]
    cfg.host_groups = {
        str(group): [str(host) for host in hosts]
        for group, hosts in (raw.get("host_groups") or {}).items()
    }
    return cfg


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load configuration from ``path``, ``FRISCH_CONFIG`` or default paths."""
    raw: dict = {}
    candidate = path or os.environ.get(ENV_PREFIX + "CONFIG")
    if candidate:
        candidates = [Path(candidate)]
        if not candidates[0].is_file():
            raise ConfigError(f"config file not found: {candidate}")
    else:
        candidates = [p for p in DEFAULT_CONFIG_PATHS if p.is_file()][:1]
    if candidates:
        with open(candidates[0]) as f:
            raw = yaml.safe_load(f) or {}

    cfg = Config()
    db = raw.get("database") or {}
    cfg.database_url = str(
        os.environ.get(ENV_PREFIX + "DATABASE_URL")
        or db.get("url")
        or cfg.database_url
    )
    cfg.git_cache_dir = Path(
        os.environ.get(ENV_PREFIX + "GIT_CACHE_DIR")
        or raw.get("git_cache_dir")
        or cfg.git_cache_dir
    )
    if raw.get("environments"):
        cfg.environments = [str(e) for e in raw["environments"]]
    static = os.environ.get(ENV_PREFIX + "WEB_STATIC")
    if static:
        cfg.web_static = Path(static)

    collectors = raw.get("collectors") or {}
    cfg.argocd = _load_argocd(collectors.get("argocd") or {})
    cfg.puppet = _load_puppet(collectors.get("puppet") or {})
    cfg.puppetdb = _load_puppetdb(collectors.get("puppetdb") or {})

    services = raw.get("services") or {}
    cfg.services = ServicesConfig(
        aliases=dict(services.get("aliases") or {}),
        display_names={
            str(k): str(v)
            for k, v in (services.get("display_names") or {}).items()
        },
        hidden=[str(s) for s in services.get("hidden", [])],
    )
    return cfg
