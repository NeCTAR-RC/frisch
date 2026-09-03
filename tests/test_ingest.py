from datetime import datetime

from sqlalchemy import select

from frisch import model
from frisch.db.models import Instance, VersionEvent
from frisch.ingest import ingest


def obs(version, ref, when, **kwargs):
    defaults = dict(
        source="argocd",
        env="test",
        source_key="main/keystone",
        service="keystone",
        version=version,
        changed_at=when,
        ref=ref,
    )
    defaults.update(kwargs)
    return model.Observation(**defaults)


T1 = datetime(2026, 1, 1)
T2 = datetime(2026, 2, 1)
T3 = datetime(2026, 3, 1)


def events(session):
    return session.scalars(
        select(VersionEvent).order_by(VersionEvent.id)
    ).all()


def instance(session):
    return session.scalar(select(Instance))


def test_change_appends_event(session):
    stats = ingest(
        session,
        "argocd",
        "test",
        [obs("1.0.0", "sha1", T1), obs("1.1.0", "sha2", T2)],
    )
    session.commit()
    assert stats.events == 2
    evts = events(session)
    assert [e.version for e in evts] == ["1.0.0", "1.1.0"]
    assert evts[0].previous_version is None
    assert evts[1].previous_version == "1.0.0"
    inst = instance(session)
    assert inst.current_version == "1.1.0"
    assert inst.current_changed_at == T2
    assert inst.active


def test_same_version_no_event(session):
    ingest(
        session,
        "argocd",
        "test",
        [obs("1.0.0", "sha1", T1), obs("1.0.0", "sha2", T2)],
    )
    session.commit()
    assert len(events(session)) == 1
    inst = instance(session)
    # A non-change commit still refreshes last_seen but not changed_at.
    assert inst.current_changed_at == T1
    assert inst.last_seen_at == T2


def test_current_meta_refreshes_without_version_change(session):
    ingest(
        session,
        "deb",
        "prod",
        [
            obs(
                "1.0",
                "r1",
                T1,
                source="deb",
                env="prod",
                source_key="pkg:mariadb-server",
                service="mariadb-server",
                meta={"mixed": True, "versions": ["1.0", "0.9"]},
            )
        ],
    )
    session.commit()
    assert instance(session).current_meta["mixed"] is True

    # Same version, but the node breakdown behind it has changed: the
    # denormalised current_meta must still refresh even though nothing
    # about current_version/current_changed_at moves.
    ingest(
        session,
        "deb",
        "prod",
        [
            obs(
                "1.0",
                "r2",
                T2,
                source="deb",
                env="prod",
                source_key="pkg:mariadb-server",
                service="mariadb-server",
                meta={"mixed": False, "versions": []},
            )
        ],
    )
    session.commit()
    inst = instance(session)
    assert inst.current_meta["mixed"] is False
    assert inst.current_changed_at == T1
    assert inst.last_seen_at == T2


def test_replay_is_idempotent(session):
    batch = [obs("1.0.0", "sha1", T1), obs("1.1.0", "sha2", T2)]
    ingest(session, "argocd", "test", batch)
    session.commit()
    stats = ingest(session, "argocd", "test", batch)
    session.commit()
    assert stats.events == 0
    assert len(events(session)) == 2
    assert instance(session).current_version == "1.1.0"


def test_tombstone(session):
    ingest(
        session,
        "argocd",
        "test",
        [obs("1.0.0", "sha1", T1), obs(None, "sha2", T2)],
    )
    session.commit()
    inst = instance(session)
    assert not inst.active
    assert inst.current_version is None
    evts = events(session)
    assert evts[-1].version is None
    assert evts[-1].previous_version == "1.0.0"


def test_readd_after_tombstone(session):
    ingest(
        session,
        "argocd",
        "test",
        [
            obs("1.0.0", "sha1", T1),
            obs(None, "sha2", T2),
            obs("2.0.0", "sha3", T3),
        ],
    )
    session.commit()
    inst = instance(session)
    assert inst.active
    assert inst.current_version == "2.0.0"
    assert len(events(session)) == 3


def test_floating_never_produces_events(session):
    ingest(
        session,
        "argocd",
        "test",
        [
            obs("HEAD", "sha1", T1, version_kind=model.FLOATING),
            obs("HEAD", "sha2", T2, version_kind=model.FLOATING),
        ],
    )
    session.commit()
    assert events(session) == []
    inst = instance(session)
    assert inst.current_version == "HEAD"
    assert inst.current_version_kind == model.FLOATING
    assert inst.active


def test_interval_start_filled_from_last_seen(session):
    ingest(
        session,
        "deb",
        "prod",
        [
            obs(
                "1.0",
                "r1",
                T1,
                source="deb",
                env="prod",
                source_key="pkg:x",
                service="x",
                precision=model.INTERVAL,
            )
        ],
    )
    ingest(
        session,
        "deb",
        "prod",
        [
            obs(
                "1.1",
                "r2",
                T2,
                source="deb",
                env="prod",
                source_key="pkg:x",
                service="x",
                precision=model.INTERVAL,
            )
        ],
    )
    session.commit()
    evts = events(session)
    assert evts[0].interval_start is None
    assert evts[1].interval_start == T1
    assert evts[1].precision == "interval"


def test_full_snapshot_tombstones_unseen(session):
    ingest(
        session,
        "deb",
        "prod",
        [
            obs(
                "1.0",
                "r1",
                T1,
                source="deb",
                env="prod",
                source_key="pkg:a",
                service="a",
            ),
            obs(
                "2.0",
                "r2",
                T1,
                source="deb",
                env="prod",
                source_key="pkg:b",
                service="b",
            ),
        ],
        full_snapshot=True,
    )
    session.commit()
    stats = ingest(
        session,
        "deb",
        "prod",
        [
            obs(
                "1.0",
                "r3",
                T2,
                source="deb",
                env="prod",
                source_key="pkg:a",
                service="a",
            )
        ],
        full_snapshot=True,
        snapshot_ref="snap:t2",
    )
    session.commit()
    assert stats.tombstones == 1
    gone = session.scalar(
        select(Instance).where(Instance.source_key == "pkg:b")
    )
    assert not gone.active
    # The other env/source is untouched by the snapshot sweep.
    still = session.scalar(
        select(Instance).where(Instance.source_key == "pkg:a")
    )
    assert still.active
