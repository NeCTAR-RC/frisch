class FrischError(Exception):
    """Base class for frisch errors."""


class ConfigError(FrischError):
    """The configuration is missing or invalid."""


class GitError(FrischError):
    """A git plumbing call failed."""


class SourceUnavailable(FrischError):
    """A live source cannot be queried right now; prior state is kept."""


class BackfillUnsupported(FrischError):
    """The source has no external history to backfill from."""
