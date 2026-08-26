from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from frisch.db.models import Base


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def session(session_factory):
    with session_factory() as sess:
        yield sess


class GitRepoBuilder:
    """A scratch git repo with deterministic commit dates."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True)
        self._git("init", "-q", "-b", "master")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.org")

    def _git(self, *args: str, env: dict | None = None) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    def commit(
        self,
        files: dict[str, str | None],
        message: str = "change",
        date: str = "2026-01-01T00:00:00 +0000",
    ) -> str:
        import os

        for rel, content in files.items():
            target = self.path / rel
            if content is None:
                self._git("rm", "-q", rel)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        self._git("add", "-A")
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
        self._git("commit", "-q", "--allow-empty", "-m", message, env=env)
        return self._git("rev-parse", "HEAD").strip()

    def move(
        self,
        old: str,
        new: str,
        message: str = "move",
        date: str = "2026-01-01T00:00:00 +0000",
    ) -> str:
        import os

        (self.path / new).parent.mkdir(parents=True, exist_ok=True)
        self._git("mv", old, new)
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
        self._git("commit", "-q", "-m", message, env=env)
        return self._git("rev-parse", "HEAD").strip()


@pytest.fixture
def make_repo(tmp_path):
    def _make(name: str = "repo") -> GitRepoBuilder:
        return GitRepoBuilder(tmp_path / name)

    return _make


class DictCursorStore:
    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def cursors():
    return DictCursorStore()
