from datetime import datetime
from pathlib import Path

from frisch.collectors.gitrepo import GitMirror


def mirror_for(repo, tmp_path) -> GitMirror:
    m = GitMirror(tmp_path / "cache", "test", str(repo.path))
    m.sync()
    return m


def test_log_and_show(make_repo, tmp_path):
    repo = make_repo()
    sha1 = repo.commit(
        {"apps/a.yaml": "v: 1\n"},
        "add a",
        date="2026-01-01T10:00:00 +1100",
    )
    sha2 = repo.commit(
        {"apps/a.yaml": "v: 2\n"}, "bump a", date="2026-01-02T00:00:00 +0000"
    )
    mirror = mirror_for(repo, tmp_path)

    commits = mirror.log(None, ["apps"])
    assert [c.sha for c in commits] == [sha1, sha2]
    assert commits[0].subject == "add a"
    assert commits[0].changes[0].status == "A"
    assert commits[1].changes[0].status == "M"
    # Author times normalised to naive UTC.
    assert commits[0].when == datetime(2025, 12, 31, 23, 0, 0)
    assert mirror.show(sha1, "apps/a.yaml") == b"v: 1\n"
    assert mirror.show(sha1, "apps/missing.yaml") is None
    assert mirror.tip() == sha2


def test_log_rename(make_repo, tmp_path):
    repo = make_repo()
    repo.commit({"apps/a.yaml": "v: 1\n"}, "add")
    sha2 = repo.move("apps/a.yaml", "apps-main/a.yaml", "restructure")
    mirror = mirror_for(repo, tmp_path)

    commits = mirror.log(None, ["apps", "apps-main"])
    change = commits[-1].changes[0]
    assert commits[-1].sha == sha2
    assert change.status == "R"
    assert change.old_path == "apps/a.yaml"
    assert change.path == "apps-main/a.yaml"


def test_incremental_and_cursor(make_repo, tmp_path):
    repo = make_repo()
    sha1 = repo.commit({"apps/a.yaml": "v: 1\n"}, "one")
    mirror = mirror_for(repo, tmp_path)
    assert len(mirror.log(None, ["apps"])) == 1

    sha2 = repo.commit({"apps/a.yaml": "v: 2\n"}, "two")
    mirror.sync()
    incremental = mirror.log(sha1, ["apps"])
    assert [c.sha for c in incremental] == [sha2]
    assert mirror.resolve_cursor(sha1) == sha1
    # A sha that is not an ancestor (bogus) drops the cursor.
    bogus = "0" * 40
    assert mirror.resolve_cursor(bogus) is None


def test_ls_files(make_repo, tmp_path):
    repo = make_repo()
    sha = repo.commit(
        {"apps/a.yaml": "v: 1\n", "apps/b.yaml": "v: 1\n", "other/c": "x"},
        "add",
    )
    mirror = mirror_for(repo, tmp_path)
    assert mirror.ls_files(sha, ["apps"]) == ["apps/a.yaml", "apps/b.yaml"]


def test_clone_creates_cache_dir(make_repo, tmp_path):
    repo = make_repo()
    repo.commit({"f": "x"}, "add")
    cache = tmp_path / "deep" / "cache"
    mirror = GitMirror(cache, "name", str(repo.path))
    mirror.sync()
    assert Path(mirror.path).is_dir()
    mirror.sync()  # second sync fetches
