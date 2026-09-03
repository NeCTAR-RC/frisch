"""Config layering: defaults, file, then FRISCH_* environment overrides."""

import os
from pathlib import Path

import pytest

from frisch import config as config_mod
from frisch.config import (
    Config,
    ENV_PREFIX,
    PuppetDBEnvConfig,
    load_config,
)
from frisch.errors import ConfigError

FULL = """
database:
  url: sqlite:////tmp/from-file.db
git_cache_dir: /var/cache/frisch/git
environments: [test, prod, dev]
collectors:
  argocd:
    repos:
      test:
        url: git@example.org:ops/apps-test.git
      prod:
        url: git@example.org:ops/apps-prod.git
        branch: main
    watched_dirs: [apps-main, apps-capi]
    cluster_by_dir:
      apps-main: main
      apps-capi: capi
    default_cluster: main
    fanout_services: [aardvark]
    app_overrides:
      event-exporter:
        values_version_paths: [image.tag]
  puppet:
    sites:
      test:
        url: git@example.org:ops/puppet-site-test
        files: [data/nodegroups/tier1.yaml]
    hieradata:
      url: git@example.org:ops/hieradata
      env_files:
        test: [testing.yaml, common.yaml]
      extra_keys:
        profile::core::gnocchi::server::image_tag: gnocchi
  puppetdb:
    environments:
      test:
        base_url: https://puppetdb.test.example.org
        verify: false
        timeout: 30
    package_patterns: ['^nectar-']
    package_names: [python3-langstroth]
services:
  aliases:
    langstroth:
      deb: [python3-langstroth]
  display_names:
    keystone: Keystone
  hidden: [internal-thing]
"""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No developer FRISCH_* variables and no host config files."""
    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATHS", ())


def write(tmp_path, text: str) -> Path:
    path = tmp_path / "frisch.yaml"
    path.write_text(text)
    return path


def test_defaults_without_any_config_file():
    cfg = load_config()
    assert cfg.database_url == Config().database_url
    assert cfg.environments == ["test", "prod"]
    assert cfg.argocd.watched_dirs == config_mod.DEFAULT_WATCHED_DIRS
    assert cfg.argocd.default_cluster == "main"
    assert cfg.puppet.hieradata is None
    assert cfg.puppetdb.environments == {}
    assert cfg.web_static is None


def test_missing_config_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(tmp_path / "nope.yaml")


def test_default_paths_are_searched(tmp_path, monkeypatch):
    path = write(tmp_path, "environments: [staging]\n")
    monkeypatch.setattr(
        config_mod, "DEFAULT_CONFIG_PATHS", (tmp_path / "gone.yaml", path)
    )
    assert load_config().environments == ["staging"]


def test_config_path_from_environment(tmp_path, monkeypatch):
    path = write(tmp_path, "environments: [staging]\n")
    monkeypatch.setenv(ENV_PREFIX + "CONFIG", str(path))
    assert load_config().environments == ["staging"]


def test_empty_config_file_falls_back_to_defaults(tmp_path):
    cfg = load_config(write(tmp_path, ""))
    assert cfg.environments == ["test", "prod"]


def test_full_config_file(tmp_path):
    cfg = load_config(write(tmp_path, FULL))

    assert cfg.database_url == "sqlite:////tmp/from-file.db"
    assert cfg.git_cache_dir == Path("/var/cache/frisch/git")
    assert cfg.environments == ["test", "prod", "dev"]

    assert cfg.argocd.repos["prod"].url == "git@example.org:ops/apps-prod.git"
    assert cfg.argocd.repos["prod"].branch == "main"
    assert cfg.argocd.repos["test"].branch == "master"
    assert cfg.argocd.watched_dirs == ["apps-main", "apps-capi"]
    assert cfg.argocd.cluster_by_dir == {
        "apps-main": "main",
        "apps-capi": "capi",
    }
    assert cfg.argocd.fanout_services == ["aardvark"]
    assert cfg.argocd.app_overrides["event-exporter"] == {
        "values_version_paths": ["image.tag"]
    }

    site = cfg.puppet.sites["test"]
    assert site.repo.url == "git@example.org:ops/puppet-site-test"
    assert site.files == ["data/nodegroups/tier1.yaml"]
    assert cfg.puppet.hieradata.env_files == {
        "test": ["testing.yaml", "common.yaml"]
    }
    assert cfg.puppet.hieradata.extra_keys == {
        "profile::core::gnocchi::server::image_tag": "gnocchi"
    }

    pdb = cfg.puppetdb.environments["test"]
    assert pdb.base_url == "https://puppetdb.test.example.org"
    assert pdb.verify is False
    assert pdb.timeout == 30.0
    assert cfg.puppetdb.package_patterns == ["^nectar-"]
    assert cfg.puppetdb.package_names == ["python3-langstroth"]

    assert cfg.services.aliases == {
        "langstroth": {"deb": ["python3-langstroth"]}
    }
    assert cfg.services.display_names == {"keystone": "Keystone"}
    assert cfg.services.hidden == ["internal-thing"]


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PREFIX + "DATABASE_URL", "sqlite:////tmp/env.db")
    monkeypatch.setenv(ENV_PREFIX + "GIT_CACHE_DIR", "/srv/git")
    monkeypatch.setenv(ENV_PREFIX + "WEB_STATIC", "/srv/www")
    monkeypatch.setenv(ENV_PREFIX + "PUPPETDB_TOKEN_TEST", "from-env")

    cfg = load_config(write(tmp_path, FULL))

    assert cfg.database_url == "sqlite:////tmp/env.db"
    assert cfg.git_cache_dir == Path("/srv/git")
    assert cfg.web_static == Path("/srv/www")
    assert cfg.puppetdb.environments["test"].token == "from-env"


@pytest.mark.parametrize(
    "text,message",
    [
        (
            "collectors:\n  argocd:\n    repos:\n      test: {branch: x}\n",
            "collectors.argocd.repos.test",
        ),
        (
            "collectors:\n  puppet:\n    sites:\n      test: {files: []}\n",
            "collectors.puppet.sites.test",
        ),
        (
            "collectors:\n  puppet:\n    hieradata: {env_files: {}}\n",
            "collectors.puppet.hieradata",
        ),
        (
            "collectors:\n  puppetdb:\n    environments:\n      test: {}\n",
            "collectors.puppetdb.environments.test",
        ),
    ],
)
def test_malformed_sections_name_their_location(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text))


def test_resolved_token_prefers_the_inline_value(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("  from-file\n")

    inline = PuppetDBEnvConfig(
        base_url="https://pdb", token="inline", token_file=str(token_file)
    )
    from_file = PuppetDBEnvConfig(
        base_url="https://pdb", token_file=str(token_file)
    )
    unset = PuppetDBEnvConfig(base_url="https://pdb")

    assert inline.resolved_token() == "inline"
    assert from_file.resolved_token() == "from-file"
    assert unset.resolved_token() is None
