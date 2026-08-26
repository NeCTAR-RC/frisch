"""Bare-mirror git plumbing shared by the git-backed collectors.

One mirror clone per source repo lives under the git cache dir; every read
is ``git show <sha>:<path>`` against it, so there is never a working tree.
Only subprocess plumbing is used (no GitPython): four calls cover the whole
walk -- fetch, rev-parse, log, show.

The history walk is ``git log --reverse --first-parent -M -C
--name-status``: --first-parent gives the state of the deployed branch
through Gerrit merge commits, -M/-C surface renames and copies (the historic
``apps/`` -> ``apps-main/`` move) so continuity is preserved.
"""

from __future__ import annotations

import dataclasses
import logging
import subprocess
from datetime import datetime, UTC
from pathlib import Path

from frisch.errors import GitError

LOG = logging.getLogger(__name__)

_GIT_TIMEOUT = 300
# Record separator/field separator for machine-parseable log output.
_LOG_FORMAT = "%x00%H%x1f%aI%x1f%an%x1f%s"


@dataclasses.dataclass(frozen=True)
class FileChange:
    status: str  # A, M, D, R, C (score digits stripped)
    path: str  # new path for renames/copies
    old_path: str | None = None


@dataclasses.dataclass(frozen=True)
class CommitInfo:
    sha: str
    when: datetime  # naive UTC
    author: str
    subject: str
    changes: tuple[FileChange, ...]


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(UTC).replace(tzinfo=None)


class GitMirror:
    def __init__(
        self, cache_dir: Path, name: str, url: str, branch: str = "master"
    ):
        self.url = url
        self.branch = branch
        self.path = Path(cache_dir) / f"{name}.git"

    def _git(self, *args: str, check: bool = True) -> str:
        cmd = ["git", "-C", str(self.path), *args]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_GIT_TIMEOUT
        )
        if check and proc.returncode != 0:
            raise GitError(
                f"{' '.join(cmd)} failed ({proc.returncode}): "
                f"{proc.stderr.strip()}"
            )
        return proc.stdout

    def sync(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                ["git", "clone", "--mirror", self.url, str(self.path)],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT * 4,
            )
            if proc.returncode != 0:
                raise GitError(
                    f"mirror clone of {self.url} failed: {proc.stderr.strip()}"
                )
            return
        self._git("fetch", "--prune", "origin")

    def tip(self) -> str:
        return self._git("rev-parse", self.branch).strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(self.path),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
        return proc.returncode == 0

    def show(self, sha: str, path: str) -> bytes | None:
        proc = subprocess.run(
            ["git", "-C", str(self.path), "show", f"{sha}:{path}"],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout

    def ls_files(self, sha: str, paths: list[str]) -> list[str]:
        out = self._git(
            "ls-tree", "-r", "--name-only", sha, "--", *paths, check=False
        )
        return [line for line in out.splitlines() if line.strip()]

    def log(self, cursor: str | None, paths: list[str]) -> list[CommitInfo]:
        """Commits on the branch after ``cursor`` (all history when None),
        oldest first, restricted to ``paths``."""
        range_spec = f"{cursor}..{self.branch}" if cursor else self.branch
        out = self._git(
            "log",
            "--reverse",
            "--first-parent",
            "-M",
            "-C",
            f"--format={_LOG_FORMAT}",
            "--name-status",
            range_spec,
            "--",
            *paths,
        )
        commits: list[CommitInfo] = []
        for chunk in out.split("\x00"):
            chunk = chunk.strip("\n")
            if not chunk:
                continue
            lines = chunk.split("\n")
            try:
                sha, when, author, subject = lines[0].split("\x1f", 3)
            except ValueError:
                LOG.warning("unparsable log header: %r", lines[0][:200])
                continue
            changes = []
            for line in lines[1:]:
                if not line.strip():
                    continue
                fields = line.split("\t")
                status = fields[0].rstrip("0123456789")
                if status in ("R", "C") and len(fields) >= 3:
                    changes.append(
                        FileChange(
                            status=status,
                            path=fields[2],
                            old_path=fields[1],
                        )
                    )
                elif len(fields) >= 2:
                    changes.append(FileChange(status=status, path=fields[1]))
            commits.append(
                CommitInfo(
                    sha=sha,
                    when=_utc(when),
                    author=author,
                    subject=subject,
                    changes=tuple(changes),
                )
            )
        return commits

    def resolve_cursor(self, cursor: str | None) -> str | None:
        """Drop a cursor that is no longer an ancestor of the branch tip
        (force-push); the idempotent ingest makes a full re-walk safe."""
        if cursor and not self.is_ancestor(cursor, self.tip()):
            LOG.warning(
                "cursor %s not an ancestor of %s in %s; re-walking history",
                cursor,
                self.branch,
                self.path.name,
            )
            return None
        return cursor
