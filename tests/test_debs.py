import ssl

import httpx
import pytest
import respx

from frisch.collectors.debs import _ssl_context, DebCollector
from frisch.config import Config, PuppetDBConfig, PuppetDBEnvConfig
from frisch.errors import SourceUnavailable
from frisch.model import ServiceIdentity

BASE = "https://openvoxdb.example.org:8081"


def collector(**kwargs) -> DebCollector:
    config = Config()
    config.puppetdb = PuppetDBConfig(
        environments={"prod": PuppetDBEnvConfig(base_url=BASE)},
        package_patterns=kwargs.get("patterns", ["^python3-nectar-"]),
        package_names=kwargs.get("names", ["python3-langstroth"]),
    )
    return DebCollector(config, ServiceIdentity(config.services))


def pql_responses(probe, rows):
    """Mock POST /pdb/query/v4: first call is the probe, second the query."""
    route = respx.post(f"{BASE}/pdb/query/v4")
    route.side_effect = [
        httpx.Response(200, json=probe),
        httpx.Response(200, json=rows),
    ]
    return route


@respx.mock
def test_snapshot_rollup(cursors):
    pql_responses(
        probe=[{"certname": "node1"}],
        rows=[
            {
                "certname": "api1",
                "package_name": "python3-langstroth",
                "version": "2.1.0-1",
            },
            {
                "certname": "api2",
                "package_name": "python3-langstroth",
                "version": "2.1.0-1",
            },
            {
                "certname": "api3",
                "package_name": "python3-langstroth",
                "version": "2.0.0-1",
            },
        ],
    )
    result = collector().collect("prod", cursors)
    assert result.full_snapshot
    (obs,) = result.observations
    assert obs.source_key == "pkg:python3-langstroth"
    assert obs.instance is None
    assert obs.service == "langstroth"
    # Headline is the majority version; the full rollup is in meta.
    assert obs.version == "2.1.0-1"
    assert obs.precision == "interval"
    assert obs.meta["mixed"] is True
    assert obs.meta["versions"] == [
        {
            "version": "2.1.0-1",
            "node_count": 2,
            "nodes": ["api1", "api2"],
        },
        {"version": "2.0.0-1", "node_count": 1, "nodes": ["api3"]},
    ]
    assert obs.meta["node_count"] == 3


@respx.mock
def test_host_groups_split_into_separate_instances(cursors):
    """A galera-style cluster stays one instance; a host outside any
    configured group keeps the pre-grouping collapsed identity."""
    pql_responses(
        probe=[{"certname": "db1"}],
        rows=[
            {
                "certname": "db1",
                "package_name": "mariadb-server",
                "version": "1:10.6.18-0+deb11u1",
            },
            {
                "certname": "db2",
                "package_name": "mariadb-server",
                "version": "1:10.6.18-0+deb11u1",
            },
            {
                "certname": "db3",
                "package_name": "mariadb-server",
                "version": "1:10.6.17-0+deb11u1",
            },
            {
                "certname": "db4",
                "package_name": "mariadb-server",
                "version": "1:10.6.12-0+deb11u1",
            },
        ],
    )
    config = Config()
    config.puppetdb = PuppetDBConfig(
        environments={"prod": PuppetDBEnvConfig(base_url=BASE)},
        package_names=["mariadb-server"],
        host_groups={"galera-db": ["db1", "db2", "db3"]},
    )
    result = DebCollector(config, ServiceIdentity(config.services)).collect(
        "prod", cursors
    )
    by_instance = {obs.instance: obs for obs in result.observations}
    assert set(by_instance) == {"galera-db", None}

    galera = by_instance["galera-db"]
    assert galera.source_key == "pkg:mariadb-server@galera-db"
    assert galera.version == "1:10.6.18-0+deb11u1"
    assert galera.meta["mixed"] is True
    assert galera.meta["node_count"] == 3

    standalone = by_instance[None]
    assert standalone.source_key == "pkg:mariadb-server"
    assert standalone.version == "1:10.6.12-0+deb11u1"
    assert standalone.meta["mixed"] is False
    assert standalone.meta["node_count"] == 1


@respx.mock
def test_empty_probe_is_unavailable(cursors):
    respx.post(f"{BASE}/pdb/query/v4").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(SourceUnavailable, match="package_inventory"):
        collector().collect("prod", cursors)


@respx.mock
def test_http_error_is_unavailable(cursors):
    respx.post(f"{BASE}/pdb/query/v4").mock(return_value=httpx.Response(403))
    with pytest.raises(SourceUnavailable, match="query failed"):
        collector().collect("prod", cursors)


def test_no_filter_is_unavailable(cursors):
    with pytest.raises(SourceUnavailable, match="configured"):
        collector(patterns=[], names=[]).collect("prod", cursors)


def test_backfill_unsupported(cursors):
    from frisch.errors import BackfillUnsupported

    with pytest.raises(BackfillUnsupported):
        collector().backfill("prod", cursors)


@respx.mock
def test_pql_filter_built_from_config(cursors):
    import json

    route = pql_responses(probe=[{"certname": "n"}], rows=[])
    collector().collect("prod", cursors)
    query = json.loads(route.calls[1].request.content)["query"]
    assert 'package_name ~ "^python3-nectar-"' in query
    assert 'package_name = "python3-langstroth"' in query


def test_ssl_context_verifies_without_strict_profile_checks():
    """Puppet CA chains commonly violate the RFC 5280 profile (e.g.
    basicConstraints not marked critical), which Python 3.13+ rejects by
    default; verification must stay on with only the strict checks off.
    """
    ctx = _ssl_context(PuppetDBEnvConfig(base_url=BASE))
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname
    assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT
    assert ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_ssl_context_verify_false_disables_verification():
    ctx = _ssl_context(PuppetDBEnvConfig(base_url=BASE, verify=False))
    assert ctx.verify_mode == ssl.CERT_NONE
    assert not ctx.check_hostname
