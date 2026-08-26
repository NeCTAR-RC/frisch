from pathlib import Path

from frisch.collectors.puppet import PuppetCollector, scan_image_tags
from frisch.config import (
    Config,
    GitRepoConfig,
    HieradataConfig,
    PuppetConfig,
    PuppetSiteConfig,
)
from frisch import model
from frisch.model import ServiceIdentity

FIXTURES = Path(__file__).parent / "fixtures" / "puppet"


def test_scan_image_tags_fixture():
    found = scan_image_tags((FIXTURES / "tier1.yaml").read_bytes())
    glance = found["nectar::profile::glance::api::image_tag"]
    assert glance["value"] == "30.2.0-7-gf1366dff0-4-2"
    assert glance["dep"] == "glance"
    assert glance["pkg"] == (
        "registry.rc.nectar.org.au/kolla/ubuntu-source-glance-api"
    )
    # Unquoted value, annotation present.
    nova = found["nectar::profile::nova::api::image_tag"]
    assert nova["value"] == "29.4.0-26-g22f2702050-10-34"
    # No annotation at all (non-semver tag), still collected.
    neutron = found["nectar::profile::neutron::server::image_tag"]
    assert neutron["value"] == "2024.1-eom-26-gc1dd7ae343-10-21"
    assert neutron["dep"] is None
    # The ENC blob and unrelated keys are not picked up.
    assert len(found) == 3


def site_collector(tmp_path, repo, files=None) -> PuppetCollector:
    config = Config()
    config.git_cache_dir = tmp_path / "cache"
    config.puppet = PuppetConfig(
        sites={
            "prod": PuppetSiteConfig(
                repo=GitRepoConfig(url=str(repo.path)),
                files=files or ["data/nodegroups/tier1.yaml"],
            )
        }
    )
    return PuppetCollector(config, ServiceIdentity(config.services))


GLANCE = (
    "# renovate: depName=glance packageName=registry.example/glance-api\n"
    "nectar::profile::glance::api::image_tag: '{version}'\n"
)


def test_site_walk_versions_and_tombstone(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {"data/nodegroups/tier1.yaml": GLANCE.format(version="1.0-1")},
        "add glance",
        date="2026-01-01T00:00:00 +0000",
    )
    repo.commit(
        {"data/nodegroups/tier1.yaml": GLANCE.format(version="1.1-2")},
        "Bump glance image to 1.1-2",
        date="2026-02-01T00:00:00 +0000",
    )
    repo.commit(
        {"data/nodegroups/tier1.yaml": "unrelated: value\n"},
        "drop glance",
        date="2026-03-01T00:00:00 +0000",
    )
    coll = site_collector(tmp_path, repo)
    result = coll.collect("prod", cursors)

    key = "nectar::profile::glance::api::image_tag"
    obs = [o for o in result.observations if o.source_key == key]
    assert [o.version for o in obs] == ["1.0-1", "1.1-2", None]
    assert obs[0].service == "glance"
    assert obs[1].meta["package_name"] == "registry.example/glance-api"
    assert obs[1].meta["subject"] == "Bump glance image to 1.1-2"
    assert cursors.get("puppet:site:prod") is not None


def test_site_incremental(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {"data/nodegroups/tier1.yaml": GLANCE.format(version="1.0-1")}, "add"
    )
    coll = site_collector(tmp_path, repo)
    coll.collect("prod", cursors)
    repo.commit(
        {"data/nodegroups/tier1.yaml": GLANCE.format(version="1.1-1")},
        "bump",
    )
    result = coll.collect("prod", cursors)
    assert [o.version for o in result.observations] == ["1.1-1"]


def test_key_moves_between_files(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {
            "data/nodegroups/tier1.yaml": GLANCE.format(version="1.0-1"),
            "data/nodegroups/tier2.yaml": "other: x\n",
        },
        "add",
    )
    repo.commit(
        {
            "data/nodegroups/tier1.yaml": "other: x\n",
            "data/nodegroups/tier2.yaml": GLANCE.format(version="1.0-1"),
        },
        "move key to tier2",
    )
    coll = site_collector(
        tmp_path,
        repo,
        files=["data/nodegroups/tier1.yaml", "data/nodegroups/tier2.yaml"],
    )
    result = coll.collect("prod", cursors)
    key = "nectar::profile::glance::api::image_tag"
    obs = [o for o in result.observations if o.source_key == key]
    # Same source_key across the move: no tombstone, version unchanged.
    assert [o.version for o in obs] == ["1.0-1", "1.0-1"]


def hieradata_collector(tmp_path, repo) -> PuppetCollector:
    config = Config()
    config.git_cache_dir = tmp_path / "cache"
    config.puppet = PuppetConfig(
        hieradata=HieradataConfig(
            repo=GitRepoConfig(url=str(repo.path)),
            env_files={
                "prod": ["common.yaml"],
                "test": ["testing.yaml", "common.yaml"],
            },
            extra_keys={
                "profile::core::gnocchi::server::image_tag": "gnocchi"
            },
        )
    )
    return PuppetCollector(config, ServiceIdentity(config.services))


GNOCCHI = "profile::core::gnocchi::server::image_tag: '{version}'\n"


def test_hieradata_extra_keys_and_precedence(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {
            "common.yaml": GNOCCHI.format(version="4.2.4-1")
            + "unrelated::image_tag: 'not-collected'\n",
            "testing.yaml": "something: else\n",
        },
        "seed",
    )
    coll = hieradata_collector(tmp_path, repo)

    prod = coll.collect("prod", cursors)
    key = "profile::core::gnocchi::server::image_tag"
    (prod_obs,) = [o for o in prod.observations if o.source_key == key]
    assert prod_obs.version == "4.2.4-1"
    assert prod_obs.service == "gnocchi"
    # Only extra_keys are collected from hieradata.
    assert all(o.source_key == key for o in prod.observations)

    # test env: no key in testing.yaml -> inherited from common.yaml.
    test_result = coll.collect("test", cursors)
    (test_obs,) = [o for o in test_result.observations if o.source_key == key]
    assert test_obs.version == "4.2.4-1"
    assert test_obs.meta.get("inherited") is True

    # An override landing in testing.yaml wins and is not inherited.
    repo.commit(
        {"testing.yaml": GNOCCHI.format(version="4.3.0-1")}, "test bump"
    )
    test_result = coll.collect("test", cursors)
    (test_obs,) = [o for o in test_result.observations if o.source_key == key]
    assert test_obs.version == "4.3.0-1"
    assert "inherited" not in test_obs.meta


def test_environments_listing(tmp_path, make_repo):
    repo = make_repo()
    coll = hieradata_collector(tmp_path, repo)
    assert coll.environments() == ["prod", "test"]
    assert coll.cursor_keys("test") == ["puppet:hieradata:test"]


def test_unannotated_key_uses_fallback_identity(make_repo, tmp_path, cursors):
    repo = make_repo()
    repo.commit(
        {
            "data/nodegroups/tier1.yaml": (
                "nectar::profile::neutron::server::image_tag: '2024.1-eom-1'\n"
            )
        },
        "add",
    )
    result = site_collector(tmp_path, repo).collect("prod", cursors)
    (obs,) = result.observations
    assert obs.service == "neutron"
    assert obs.version == "2024.1-eom-1"
    assert obs.version_kind == model.PINNED
