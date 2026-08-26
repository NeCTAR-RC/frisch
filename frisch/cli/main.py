from __future__ import annotations

import sys

from cliff.app import App
from cliff.commandmanager import CommandManager

from frisch import __version__


class FrischApp(App):
    def __init__(self):
        super().__init__(
            description="Deployment version visibility for Nectar",
            version=__version__,
            command_manager=CommandManager("frisch.cli"),
            deferred_help=True,
        )


def main(argv: list[str] | None = None) -> int:
    return FrischApp().run(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
