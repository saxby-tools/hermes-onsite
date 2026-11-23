"""The commands a person types: `/onsite`, `/run` and `hermes onsite`."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys

from pathlib import Path

from . import dialout
from . import mcptools
from . import messages
from . import mirror
from . import screen
from . import wizard
from .config import ConfigError
from .config import KIND_LOCAL
from .config import TargetSpec
from .health import probe_target
from .proposals import STATUS_SENT
from .runtime import CLEAR_OVERRIDE
from .runtime import Runtime
from .runtime import UnknownTarget


logger = logging.getLogger(__name__)

MAIN_COMMAND = "onsite"
RUN_COMMAND = "run"

MAIN_HELP = "Show or switch the machine this session's tools run on"
RUN_HELP = "Ask the agent to run a command it proposed"
CLI_HELP = "Check and set up the onsite targets"

MODE_DRY = "[DRY RUN]"
MODE_LIVE = "[LIVE]"

EXIT_OK = 0
EXIT_DOWN = 1
EXIT_USAGE = 2

TARGET_FOLDER_SEPARATOR = ":"

NEXT_STEPS = (
    "Next:",
    "  hermes plugins enable onsite",
    "  hermes config set terminal.backend onsite",
    "  hermes onsite status",
)


def session_folder(ctx) -> str:
    # In the classic CLI this is the folder Hermes was started in.
    # In a gateway session it is empty: see `main_command`.
    try:
        return ctx.session_cwd() or ""
    except Exception:
        logger.debug("onsite: no session folder", exc_info=True)
        return ""


def session_cache_key() -> str:
    from .compat import environment_key

    try:
        return environment_key()
    except Exception:
        logger.debug("onsite: no environment key", exc_info=True)
        return ""


def drop_environment(*ids: str) -> None:
    from .compat import drop_current_environment

    try:
        drop_current_environment(*ids)
    except Exception:
        # The pin is already saved. At worst the old environment
        # lives until Hermes reaps it, a few minutes later.
        logger.warning("onsite: could not drop the environment", exc_info=True)


def build_onsite_command(ctx, runtime: Runtime):
    def main_command(
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

    return main_command


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
        mirror=args.mirror,
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


def mirror_targets(config, name: str | None) -> list[TargetSpec]:
    """The targets one `hermes onsite mirror` run covers."""

    if name is None:
        return [spec for spec in config.targets.values() if spec.mirror]

    if name not in config.targets:
        return []

    return [config.target(name)]


def cli_mirror(runtime: Runtime, args) -> int:
    try:
        config = runtime.load_config()
    except ConfigError as error:
        print(error)
        return EXIT_USAGE

    specs = mirror_targets(config, args.target)

    if not specs:
        print(messages.MIRROR_NOT_SET)
        return EXIT_USAGE

    mode = MODE_LIVE if args.apply else MODE_DRY
    code = EXIT_OK

    for spec in specs:
        depth = args.depth or spec.mirror_depth

        try:
            result = mirror.sync(spec, depth, args.apply)
        except mirror.MirrorError as error:
            print(f"{mode} {spec.label}: {error}")
            code = EXIT_DOWN
            continue

        print(messages.format_mirror_result(
            mode, spec, depth, result, args.apply,
        ))

    return code


def split_target_folder(value: str) -> tuple[str, str] | None:
    name, separator, folder = value.partition(TARGET_FOLDER_SEPARATOR)

    if not separator:
        return None

    if not name or not folder:
        return None

    return name, folder


def cli_open(runtime: Runtime, args) -> int:
    pair = split_target_folder(args.folder)

    if pair is None:
        print(messages.OPEN_USAGE)
        return EXIT_USAGE

    name, target_folder = pair

    try:
        config = runtime.load_config()
    except ConfigError as error:
        print(error)
        return EXIT_USAGE

    if name not in config.targets:
        print(f"`{name}` is not a target.")
        return EXIT_USAGE

    spec = config.target(name)
    mode = MODE_LIVE if args.apply else MODE_DRY

    try:
        folder, result = mirror.open_folder(spec, target_folder, args.apply)
    except mirror.MirrorError as error:
        print(f"{mode} {error}")
        return EXIT_DOWN

    print(messages.format_open_result(
        mode, spec, target_folder, str(folder), result, args.apply,
    ))

    if result.skipped:
        return EXIT_USAGE

    return EXIT_OK


def cli_mcp(runtime: Runtime, args) -> int:
    try:
        config = runtime.load_config()
    except ConfigError as error:
        print(error)
        return EXIT_USAGE

    if args.target not in config.targets:
        print(f"`{args.target}` is not a target.")
        return EXIT_USAGE

    spec = config.target(args.target)
    words = list(args.server_command)
    mode = MODE_LIVE if args.apply else MODE_DRY

    try:
        call = mcptools.add_command(spec, args.name, words)
        server = mcptools.server_name(spec, args.name)
    except mcptools.McpError as error:
        print(error)
        return EXIT_USAGE

    print(messages.format_mcp_add(
        mode,
        spec,
        server,
        shlex.join(words),
        shlex.join(call),
        args.apply,
    ))

    if not args.apply:
        return EXIT_OK

    # Hermes owns the MCP settings and may ask which tools to keep,
    # so its own command does the adding. It inherits this profile.
    call[0] = shutil.which(mcptools.HERMES_BINARY) or call[0]

    return subprocess.run(call, check=False).returncode


def cli_screen(runtime: Runtime, args) -> int:
    try:
        config = runtime.load_config()
    except ConfigError as error:
        print(error)
        return EXIT_USAGE

    if args.target not in config.targets:
        print(f"`{args.target}` is not a target.")
        return EXIT_USAGE

    spec = config.target(args.target)

    if spec.kind == KIND_LOCAL:
        print(
            f"`{spec.name}` is this machine. Check its screen with "
            "`hermes computer-use doctor`."
        )
        return EXIT_USAGE

    try:
        seat = screen.read_seat(spec)
    except screen.ScreenError as error:
        print(error)
        return EXIT_DOWN

    problems = screen.find_problems(seat)

    print(messages.format_screen_report(
        spec, seat, problems, screen.find_notes(seat),
    ))

    if problems:
        return EXIT_DOWN

    return EXIT_OK


def cli_screen_install(args) -> int:
    from .compat import plugins_dir

    # Hermes finds a screen backend by the name of its folder, in the
    # same folder it loads this plugin from.
    source = Path(__file__).resolve().parent / messages.SCREEN_PLUGIN
    link = plugins_dir() / messages.SCREEN_PLUGIN
    mode = MODE_LIVE if args.apply else MODE_DRY

    present = link.exists() or link.is_symlink()
    in_place = present and link.resolve() == source
    blocked = present and not in_place

    if args.apply and not present:
        link.symlink_to(source, target_is_directory=True)

    print(messages.format_screen_install(
        mode, str(link), str(source), in_place, blocked, args.apply,
    ))

    if blocked:
        return EXIT_USAGE

    return EXIT_OK


def cli_dial_out(ctx, args) -> int:
    name = args.target
    mode = MODE_LIVE if args.apply else MODE_DRY

    try:
        command = dialout.tunnel_command(args.server, args.port)
        unit = dialout.service_unit(args.server, args.port)
        block = dialout.target_settings(args.user, args.port)
        wizard.validate({"default": name, "targets": {name: block}})
    except (dialout.DialOutError, ConfigError) as error:
        print(error)
        return EXIT_USAGE

    if args.apply:
        ctx.set_config("targets", {name: block})

    print(messages.format_dial_out(
        mode,
        name,
        args.port,
        command,
        unit,
        wizard.render_yaml({"targets": {name: block}}),
        args.apply,
    ))

    return EXIT_OK


def setup_cli(parser) -> None:
    actions = parser.add_subparsers(dest="onsite_action")

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
        "--mirror",
        action="append",
        default=[],
        metavar="NAME=/HOST/FOLDER",
        help="a folder on this machine that mirrors NAME's folder names.",
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

    mirrored = actions.add_parser(
        "mirror",
        help="copy a target's folder names, not its files, to this "
        "machine. A dry run unless --apply is given.",
    )
    mirrored.add_argument(
        "target",
        nargs="?",
        help="the target to mirror. Default: every target with a mirror.",
    )
    mirrored.add_argument(
        "--depth",
        type=int,
        metavar="LEVELS",
        help="how many levels under the home folder. Default: 3.",
    )
    mirrored.add_argument(
        "--apply",
        action="store_true",
        help="make the folders. Without it, they are only counted.",
    )

    opened = actions.add_parser(
        "open",
        help="make one folder on a target openable from this machine. "
        "A dry run unless --apply is given.",
    )
    opened.add_argument(
        "folder",
        metavar="TARGET:FOLDER",
        help="a folder on a target, such as laptop:~/work/site.",
    )
    opened.add_argument(
        "--apply",
        action="store_true",
        help="make the folder. Without it, its place is only shown.",
    )

    served = actions.add_parser(
        "mcp",
        help="show how to add an MCP server that runs on a target.",
    )
    served.add_argument("target", help="the target the server runs on.")
    served.add_argument("name", help="a short name for the server.")
    served.add_argument(
        "--apply",
        action="store_true",
        help="add the server. Without it, the call is only shown.",
    )
    served.add_argument(
        "server_command",
        nargs="*",
        metavar="-- COMMAND",
        help="after `--`, the command that starts the server there.",
    )

    seen = actions.add_parser(
        "screen",
        help="check whether a target's screen can be driven, and what "
        "is missing. Changes nothing.",
    )
    seen.add_argument("target", help="the target to check.")

    installed = actions.add_parser(
        "screen-install",
        help="put the screen backend where Hermes looks for it. A "
        "dry run unless --apply is given.",
    )
    installed.add_argument(
        "--apply",
        action="store_true",
        help="make the link. Without it, it is only shown.",
    )

    dialed = actions.add_parser(
        "dial-out",
        help="set up a machine this host cannot reach, which dials "
        "out to it. A dry run unless --apply is given.",
    )
    dialed.add_argument("target", help="a name for the machine.")
    dialed.add_argument(
        "--server",
        required=True,
        metavar="USER@ADDRESS",
        help="this host, as the machine logs in to it.",
    )
    dialed.add_argument(
        "--user",
        required=True,
        help="the account on the machine that tool calls run as.",
    )
    dialed.add_argument(
        "--port",
        type=int,
        default=dialout.DEFAULT_PORT,
        help="the port on this host that leads to the machine. "
        f"Default: {dialout.DEFAULT_PORT}.",
    )
    dialed.add_argument(
        "--apply",
        action="store_true",
        help="write the target's settings. Without it, they are shown.",
    )


def build_cli_handler(ctx, runtime: Runtime):
    def handle_cli(args) -> int:
        action = getattr(args, "onsite_action", None)

        if action == "init":
            return cli_init(ctx, args)

        if action == "mirror":
            return cli_mirror(runtime, args)

        if action == "open":
            return cli_open(runtime, args)

        if action == "dial-out":
            return cli_dial_out(ctx, args)

        if action == "mcp":
            return cli_mcp(runtime, args)

        if action == "screen":
            return cli_screen(runtime, args)

        if action == "screen-install":
            return cli_screen_install(args)

        return cli_status(runtime, args)

    return handle_cli


def register(ctx, runtime: Runtime) -> None:
    ctx.register_command(
        MAIN_COMMAND,
        build_onsite_command(ctx, runtime),
        description=MAIN_HELP,
        args_hint="[target|auto]",
    )
    ctx.register_command(
        RUN_COMMAND,
        build_run_command(ctx, runtime),
        description=RUN_HELP,
        args_hint="<number>",
    )
    ctx.register_cli_command(
        MAIN_COMMAND,
        CLI_HELP,
        setup_cli,
        build_cli_handler(ctx, runtime),
        description=CLI_HELP,
    )
