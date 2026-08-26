from importlib import metadata

try:
    __version__ = metadata.version("frisch")
except metadata.PackageNotFoundError:  # pragma: no cover - not installed
    __version__ = "0.0.0"
