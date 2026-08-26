from frisch.config import ServicesConfig
from frisch import model


def test_argocd_fanout_identity():
    fanout = ["aardvark", "ceilometer"]
    assert model.argocd_default_identity("aardvark-qld", "main", fanout) == (
        "aardvark",
        "qld",
    )
    assert model.argocd_default_identity("aardvark", "main", fanout) == (
        "aardvark",
        None,
    )
    # Never split un-listed stems on '-'.
    assert model.argocd_default_identity("dashboard-next", "main", fanout) == (
        "dashboard-next",
        None,
    )


def test_argocd_capi_instance():
    assert model.argocd_default_identity("prometheus", "capi", []) == (
        "prometheus",
        "capi",
    )
    assert model.argocd_default_identity("prometheus", "main", []) == (
        "prometheus",
        None,
    )


def test_puppet_fallback_identity():
    assert (
        model.puppet_default_identity(
            "nectar::profile::neutron::server::image_tag"
        )
        == "neutron"
    )
    assert (
        model.puppet_default_identity(
            "profile::core::gnocchi::server::image_tag"
        )
        == "gnocchi"
    )
    assert model.puppet_default_identity("unrelated::key") is None


def test_deb_identity():
    assert model.deb_default_identity("python3-langstroth") == "langstroth"
    assert model.deb_default_identity("nectar-tools") == "nectar-tools"


def test_alias_resolution():
    identity = model.ServiceIdentity(
        ServicesConfig(
            aliases={
                "langstroth": {"deb": ["python3-langstroth-extra"]},
                "dashboard": {"argocd": "horizon"},
            }
        )
    )
    assert identity.resolve("deb", "python3-langstroth-extra") == "langstroth"
    assert identity.resolve("argocd", "horizon") == "dashboard"
    assert identity.resolve("argocd", "keystone") == "keystone"
