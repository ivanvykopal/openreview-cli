"""Entry point for ``python -m openreview_cli``."""

from __future__ import annotations

import sys


def _force_utf8(stream: object) -> None:
    """Reconfigure a stream to UTF-8 with replacement errors.

    Windows consoles often default to cp1252, where the Unicode glyphs
    used in the CLI output (``✓``, ``→``) raise
    ``UnicodeEncodeError``. Reconfiguring to UTF-8 with
    ``errors="replace"`` keeps output printable on any console.
    """
    if stream is None or not hasattr(stream, "reconfigure"):
        return

    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - cosmetic, never fatal
        pass


def main() -> None:
    """Run the CLI, exiting 130 on Ctrl-C."""
    _force_utf8(sys.stdout)
    _force_utf8(sys.stderr)

    from .cli import cli

    try:
        cli(prog_name="openreview-cli")
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)


if __name__ == "__main__":
    main()
