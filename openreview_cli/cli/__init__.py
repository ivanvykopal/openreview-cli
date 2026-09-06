"""Click command-line interface for the OpenReview CLI.

This package contains only presentation code: option parsing, output
formatting (see :mod:`openreview_cli.cli.formatting`), and error
reporting. All business logic lives in :mod:`openreview_cli.lib`.

Subcommand modules
==================

Each subcommand lives in its own module named ``cmd_<name>.py``
(``cmd_status.py``, ``cmd_reviews.py``, ``cmd_download.py``,
``cmd_deadlines.py``, ``cmd_assignments.py``). Every module MUST follow
this exact pattern::

    import click

    from . import handle_errors
    from .formatting import emit


    @click.command("status")
    @click.option("--foo", help="...")
    @click.pass_context
    @handle_errors
    def status(ctx, foo):
        \"\"\"One-line help text shown in ``--help``.\"\"\"
        facade = get_facade(ctx)
        # ... call lib functions, then:
        emit(ctx, payload, human)

Rules for subcommand modules:

* The command function is decorated with ``@click.command("<name>")``
  plus its own options, ``@click.pass_context`` and ``@handle_errors``
  (innermost, so it wraps the function body). ``@handle_errors`` MUST be
  the decorator closest to the function.
* Get the shared context via ``ctx.obj`` (a dict, see below); build the
  facade with :func:`get_facade` (memoized), never construct a client
  directly.
* Access ``ctx.obj["as_json"]``, ``ctx.obj["venues"]``, etc.
* Do not print with bare ``print``; use :func:`openreview_cli.cli.formatting.emit`
  or the other helpers in that module.
* No business logic in this package — delegate to
  :mod:`openreview_cli.lib` modules.
"""

from __future__ import annotations

import json
from typing import Any

import click

from ..config import load_config, resolve_credentials
from ..client import OpenReviewFacade
from ..exceptions import OpenReviewCLIError

__all__ = ["cli", "handle_errors", "get_facade"]


def _build_context(
    config_path: str | None,
    username: str | None,
    password: str | None,
    venues: tuple[str, ...],
    as_json: bool,
) -> dict[str, Any]:
    """Build the shared context object stored in ``ctx.obj``.

    Loads the config, resolves the venue list (CLI ``--venue`` overrides
    the config), and records the flags needed later for facade
    construction and credential prompting.

    Raises:
        OpenReviewCLIError: If the config file is invalid.
    """
    config = load_config(config_path)
    resolved_venues = list(venues) if venues else list(config.venues)
    return {
        "config": config,
        "facade": None,
        "cli_username": username,
        "cli_password": password,
        "venues": resolved_venues,
        "as_json": as_json,
    }


def get_facade(ctx: click.Context) -> OpenReviewFacade:
    """Return the shared :class:`OpenReviewFacade` for this invocation.

    The facade is constructed lazily on first call and memoized in
    ``ctx.obj["facade"]``, so commands that never touch the API pay no
    login cost. Credentials are resolved here (CLI flag > environment
    variable > config file > prompt); prompting is disabled in JSON
    mode.

    Raises:
        OpenReviewCLIError: If credentials cannot be resolved or login
            fails.
    """
    if ctx.obj.get("facade") is None:
        obj = ctx.obj
        config = obj["config"]
        username, password = resolve_credentials(
            config,
            obj.get("cli_username"),
            obj.get("cli_password"),
            allow_prompt=not obj.get("as_json", False),
        )
        obj["facade"] = OpenReviewFacade.from_config(
            config, username, password
        )
    return ctx.obj["facade"]


def handle_errors(func):
    """Decorator that converts :class:`OpenReviewCLIError` into a clean exit.

    Human mode prints ``Error: <message>`` to stderr and exits 1;
    JSON mode prints ``{"error": "<message>"}`` to stdout and exits 1.
    Any other exception propagates unchanged (showing a traceback).

    Apply this as the decorator *closest to the function*, after
    ``@click.pass_context``.
    """

    def wrapper(*args: Any, **kwargs: Any):
        try:
            return func(*args, **kwargs)
        except OpenReviewCLIError as exc:
            as_json = False
            if args and isinstance(args[0], click.Context):
                as_json = bool((args[0].obj or {}).get("as_json"))
            if as_json:
                click.echo(json.dumps({"error": str(exc)}))
            else:
                click.secho(f"Error: {exc}", fg="red", err=True)
            raise SystemExit(1)

    return wrapper


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to config file (default ~/.openreview_cli.yaml)",
)
@click.option(
    "--username",
    default=None,
    envvar="OPENREVIEW_USERNAME",
    help="OpenReview username/email",
)
@click.option(
    "--password",
    default=None,
    envvar="OPENREVIEW_PASSWORD",
    help="OpenReview password",
)
@click.option(
    "--venue",
    "venues",
    multiple=True,
    help="Venue id(s), e.g. aclweb.org/ACL/ARR/2026/August (repeatable; default: config venues)",
)
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: str | None,
    username: str | None,
    password: str | None,
    venues: tuple[str, ...],
    as_json: bool,
) -> None:
    """OpenReview CLI - review status, deadlines, downloads and more.

    Shared options apply to every subcommand. Run
    ``openreview-cli <command> --help`` for command-specific help.
    """
    ctx.obj = _build_context(config_path, username, password, venues, as_json)


# Subcommand registration. Each module below must expose its command as a
# click.Command created with @click.command(...) and attach it here; the
# stub modules currently register nothing (Task 5 fills them). They do
# NOT need a register() function; instead they define a module-level
# ``command`` attribute (a click.Command) that this loop registers once
# they are implemented — stubs simply omit it.
from . import cmd_assignments, cmd_deadlines, cmd_download, cmd_reviews, cmd_status  # noqa: E402

for _module in (cmd_status, cmd_reviews, cmd_download, cmd_deadlines, cmd_assignments):
    _command = getattr(_module, "command", None)
    if isinstance(_command, click.Command):
        cli.add_command(_command)


if __name__ == "__main__":  # pragma: no cover
    cli(prog_name="openreview-cli")
