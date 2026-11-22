"""The commands a person types: `/hands`, `/run` and `hermes hands`."""

from __future__ import annotations

import logging
import os
import sys

from . import messages
from . import wizard
from .config import ConfigError
from .config import KIND_LOCAL
from .health import probe_target
from .proposals import STATUS_SENT
from .runtime import CLEAR_OVERRIDE
from .runtime import Runtime
from .runtime import UnknownTarget


logger = logging.getLogger(__name__)

HANDS_COMMAND = "hands"
RUN_COMMAND = "run"

HANDS_HELP = "Show or switch the machine this session's tools run on"
RUN_HELP = "Ask the agent to run a command it proposed"
CLI_HELP = "Check and set up the hermes-hands targets"

MODE_DRY = "[DRY RUN]"
MODE_LIVE = "[LIVE]"

EXIT_OK = 0
EXIT_DOWN = 1
EXIT_USAGE = 2

NEXT_STEPS = (
    "Next:",
    "  hermes plugins enable hermes-hands",
    "  hermes config set terminal.backend hands",
    "  hermes hands status",
)


def session_folder(ctx) -> str:
    # In the classic CLI this is the folder Hermes was started in.
    # In a gateway session it is empty: see `hands_command`.
    try:
        return ctx.session_cwd() or ""
    except Exception:
        logger.debug("hands: no session folder", exc_info=True)
        return ""


def session_cache_key() -> str:
    from .compat import environment_key

    try:
        return environment_key()
    except Exception:
        logger.debug("hands: no environment key", exc_info=True)
        return ""


def drop_environment(*ids: str) -> None:
    from .compat import drop_current_environment

    try:
        drop_current_environment(*ids)
    except Exception:
        # The pin is already saved. At worst the old environment
        # lives until Hermes reaps it, a few minutes later.
        logger.warning("hands: could not drop the environment", exc_info=True)


def build_hands_command(ctx, runtime: Runtime):
    def hands_command(
        raw_args: str = "",
        session_id: str = "",
        session_key: str = "",
        **kwargs,
    ):
        target = (raw_args or "").strip().split(" ")[0]
        session_id = session_id or ""
        key = session_cache_key()

        # Hermes gives a slash command no folder. The one noted when
        # the session started is used instead.
        folder = session_folder(ctx) or runtime.folders.find(
            key,
            session_id,
            session_key,
        )

        if not target:
            return messages.format_status(
                runtime.status(key, session_id, folder)
            )

        try:
            route = runtime.switch(key, target, folder, session_id)
        except (ConfigError, UnknownTarget) as error:
            return str(error)

        # The next tool call must build a new environment on the new
        # target. The old one would otherwise keep serving the session.
        drop_environment(session_id, session_key)

        spec = runtime.load_config().target(route.target)
        status = runtime.status(key, session_id, folder)

        return messages.format_switch(
            route,
            spec,
            status,
            cleared=target == CLEAR_OVERRIDE,
        )

    return hands_command


def parse_number(raw_args: str) -> int | None:
    text = (raw_args or "").strip().lstrip("#")

    if not text.isdigit():
        return None

    return int(text)


def build_run_command(ctx, runtime: Runtime):
    def run_command(
        raw_args: str = "",
        session_id: str = "",
        session_key: str = "",
        **kwargs,
    ):
        bucket = session_key or session_id or ""
        number = parse_number(raw_args)

        if number is None:
            return messages.format_run_usage(runtime.proposals.list(bucket))

        proposal = runtime.proposals.get(bucket, number)

        if proposal is None:
            return messages.format_no_proposal(
                number,
                runtime.proposals.list(bucket),
            )

        # The agent runs it, not this handler. That keeps the command
        # inside the terminal tool, where Hermes' approval gate is.
        sent = ctx.inject_message(messages.format_run_request(proposal))

        if not sent:
            return messages.format_run_blocked(proposal)

        runtime.proposals.mark(bucket, number, STATUS_SENT)

        return messages.format_run_sent(proposal)

    return run_command


def describe_row(spec) -> dict:
    probe = probe_target(spec)
    state = "up" if probe.ok else f"DOWN ({probe.detail})"

    if probe.ok and spec.kind != KIND_LOCAL:
        state = f"up ({probe.elapsed_ms} ms)  {spec.user}@{spec.host}"

    return {"name": spec.name, "kind": spec.kind, "state": state}


def cli_status(runtime: Runtime, args) -> int:
    folder = os.getcwd()
    status = runtime.status(cwd=folder)

    if not status.get("ok"):
        print(status.get("error"))
        return EXIT_USAGE

    config = runtime.load_config()
    rows = [describe_row(spec) for spec in config.targets.values()]

    print(messages.format_cli_status(folder, status, rows))

    if not status.get("connected"):
        return EXIT_DOWN

    return EXIT_OK


def wants_interview(args) -> bool:
    if args.ssh or args.local:
        return False

    return sys.stdin.isatty()


def gather_settings(args) -> dict:
    if wants_interview(args):
        return wizard.interview(input)

    return wizard.settings_from_flags(
        ssh=args.ssh,
        local=args.local,
        prefix=args.prefix,
        path_map=args.map,
        key=args.key,
        known_hosts=args.known_hosts,
        default=args.default,
    )


def cli_init(ctx, args) -> int:
    try:
        settings = gather_settings(args)
        wizard.validate(settings)
    except (wizard.FlagError, ConfigError) as error:
        print(error)
        return EXIT_USAGE

    mode = MODE_LIVE if args.apply else MODE_DRY

    print(f"{mode} settings for config.yaml:")
    print()
    print(wizard.render_yaml(settings))
    print()

    if not args.apply:
        print(f"{mode} nothing was written. Add --apply to write it.")
        return EXIT_OK

    ctx.set_config("default", settings["default"])
    ctx.set_config("targets", settings["targets"])

    print(f"{mode} written.")
    print()
    print("\n".join(NEXT_STEPS))

    return EXIT_OK


def setup_cli(parser) -> None:
    actions = parser.add_subparsers(dest="hands_action")

    actions.add_parser(
        "status",
        help="show the target for this folder and whether each is up",
    )

    init = actions.add_parser(
        "init",
        help="name your targets. A dry run unless --apply is given.",
    )
    init.add_argument(
        "--ssh",
        action="append",
        default=[],
        metavar="NAME=USER@HOST[:PORT]",
        help="a machine reached over SSH. Repeat for more.",
    )
    init.add_argument(
        "--local",
        action="append",
        default=[],
        metavar="NAME",
        help="this machine.",
    )
    init.add_argument(
        "--prefix",
        action="append",
        default=[],
        metavar="NAME=/PATH",
        help="sessions under this folder go to NAME. Repeat for more.",
    )
    init.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="NAME=/HOST/PATH=/TARGET/PATH",
        help="a folder here that is the same folder on NAME.",
    )
    init.add_argument(
        "--key",
        action="append",
        default=[],
        metavar="NAME=/PATH/TO/KEY",
        help="the private key for NAME.",
    )
    init.add_argument(
        "--known-hosts",
        action="append",
        default=[],
        metavar="NAME=/PATH/TO/FILE",
        help="a known_hosts file for NAME alone.",
    )
    init.add_argument(
        "--default",
        metavar="NAME",
        help="the target used when no folder rule matches.",
    )
    init.add_argument(
        "--apply",
        action="store_true",
        help="write the settings. Without it, they are only shown.",
    )


def build_cli_handler(ctx, runtime: Runtime):
    def handle_cli(args) -> int:
        action = getattr(args, "hands_action", None)

        if action == "init":
            return cli_init(ctx, args)

        return cli_status(runtime, args)

    return handle_cli


def register(ctx, runtime: Runtime) -> None:
    ctx.register_command(
        HANDS_COMMAND,
        build_hands_command(ctx, runtime),
        description=HANDS_HELP,
        args_hint="[target|auto]",
    )
    ctx.register_command(
        RUN_COMMAND,
        build_run_command(ctx, runtime),
        description=RUN_HELP,
        args_hint="<number>",
    )
    ctx.register_cli_command(
        HANDS_COMMAND,
        CLI_HELP,
        setup_cli,
        build_cli_handler(ctx, runtime),
        description=CLI_HELP,
    )
