"""The commands a person types: `/onsite`, `/run` and `hermes onsite`."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys

from pathlib import Path

from . import dialin
from . import mcptools
from . import messages
from . import mounts
from . import screen
from . import screenshare
from . import wizard
from .config import ConfigError
from .config import KIND_LOCAL
from .config import TargetSpec
from .config import parse_config
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

DIAL_IN_DRY_NOTE = (
    "the port picked for a new computer that dials in is picked "
    "again when this is written."
)

NEXT_STEPS = (
    "Next:",
    "  hermes config set terminal.backend onsite",
    "  hermes onsite status",
    "",
    "So that /run can hand a command to the agent in the TUI and the",
    "desktop app:",
    "  hermes config set "
    "plugins.entries.onsite.allow_gateway_injection true",
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

        if not runtime.in_use():
            return messages.NOT_IN_USE

        if not target:
            return messages.format_status(
                runtime.status(key, session_id, folder)
            )

        try:
            route = runtime.switch(
                key,
                target,
                folder,
                session_id,
                session_key or "",
            )
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


def hand_to_agent(ctx, text: str, session_key: str) -> bool:
    """Put `text` into the chat the command came from.

    The session is named whenever Hermes gave one. The desktop app's
    backend only takes a message for a session it is told by name.
    With no name it looks for a messaging gateway in its own process,
    finds none, and the Run button did nothing but print a hint.
    """

    if session_key:
        return bool(ctx.inject_message(text, session_key=session_key))

    return bool(ctx.inject_message(text))


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
        sent = hand_to_agent(
            ctx,
            messages.format_run_request(proposal),
            session_key,
        )

        if not sent:
            return messages.format_run_blocked(proposal)

        runtime.proposals.mark(bucket, number, STATUS_SENT)

        return messages.format_run_sent(proposal)

    return run_command


def describe_address(spec) -> str:
    """Where a target is, for the list `status` prints."""

    # A computer that dials in is at the server's own loopback
    # address. Its port is what tells one from another.
    if spec.dial_in:
        return f"{spec.user}, dials in on port {spec.port}"

    return f"{spec.user}@{spec.host}"


def describe_row(spec) -> dict:
    probe = probe_target(spec)
    state = "up" if probe.ok else f"DOWN ({probe.detail})"

    if probe.ok and spec.kind != KIND_LOCAL:
        state = f"up ({probe.elapsed_ms} ms)  {describe_address(spec)}"

    return {"name": spec.name, "kind": spec.kind, "state": state}


def cli_status(runtime: Runtime, args) -> int:
    folder = os.getcwd()
    status = runtime.status(cwd=folder)

    # The targets are still worth checking before the backend is
    # selected, so the list is printed. The warning comes first, and
    # the command does not report success.
    in_use = runtime.in_use()

    if not in_use:
        print(messages.NOT_IN_USE)
        print()

    if not status.get("ok"):
        print(status.get("error"))
        return EXIT_USAGE

    config = runtime.load_config()
    rows = [describe_row(spec) for spec in config.targets.values()]

    print(messages.format_cli_status(folder, status, rows))

    if not in_use:
        return EXIT_USAGE

    if not status.get("connected"):
        return EXIT_DOWN

    return EXIT_OK


def wants_interview(args) -> bool:
    if args.ssh or args.local or args.dial_in:
        return False

    return sys.stdin.isatty()


def gather_settings(runtime: Runtime, args) -> dict:
    # A computer that dials in keeps the port it was saved with.
    saved = runtime.raw_settings()["targets"]

    if wants_interview(args):
        return wizard.interview(input, saved)

    return wizard.settings_from_flags(
        ssh=args.ssh,
        local=args.local,
        prefix=args.prefix,
        path_map=args.map,
        key=args.key,
        known_hosts=args.known_hosts,
        default=args.default,
        mount=args.mount,
        start=args.start,
        dial_in=args.dial_in,
        server=args.server,
        saved=saved,
    )


def dial_in_names(settings: dict) -> list[str]:
    targets = settings.get("targets") or {}

    return [name for name, block in targets.items() if block.get("dial_in")]


def has_new_port(settings: dict, saved: dict) -> bool:
    """Whether a port was picked in this run for a computer that
    dials in. One that was saved before keeps the port it had."""

    for name in dial_in_names(settings):
        if dialin.saved_port(saved, name) is None:
            return True

    return False


def cli_init(runtime: Runtime, args) -> int:
    try:
        settings = gather_settings(runtime, args)
        wizard.validate(settings)
    except (wizard.FlagError, ConfigError) as error:
        print(error)
        return EXIT_USAGE

    mode = MODE_LIVE if args.apply else MODE_DRY

    print(f"{mode} settings for config.yaml:")
    print()
    print(wizard.render_yaml(settings))
    print()

    dialing = dial_in_names(settings)
    saved = runtime.raw_settings()["targets"]

    if not args.apply:
        print(f"{mode} nothing was written. Add --apply to write it.")

        if has_new_port(settings, saved):
            print(f"{mode} {DIAL_IN_DRY_NOTE}")

        return EXIT_OK

    # The whole block is written, so a target left out this time is
    # gone from the file and not merged back in.
    try:
        runtime.save_settings(settings)
    except RuntimeError as error:
        print(f"{mode} {error}")
        return EXIT_USAGE

    print(f"{mode} written.")
    print()
    print("\n".join(NEXT_STEPS))

    for name in dialing:
        print()
        print(f"To connect `{name}`, see: hermes onsite dial-in {name}")

    return EXIT_OK


def show_dial_in(runtime: Runtime, name: str, system: str | None) -> int:
    """Print what a person runs on a computer that dials in."""

    try:
        config = runtime.load_config()
    except ConfigError as error:
        print(error)
        return EXIT_USAGE

    spec = config.targets.get(name)

    if spec is None or not spec.dial_in:
        print(f"`{name}` is not a computer that dials in.")
        print(messages.DIAL_IN_USAGE)
        return EXIT_USAGE

    info = dialin.instructions(name, spec.dial_in, spec.port)

    if system is not None:
        print(messages.format_dial_in_keep(name, info["keep"][system]))
        return EXIT_OK

    probe = probe_target(spec)
    state = f"connected ({probe.elapsed_ms} ms)"

    if not probe.ok:
        state = f"not connected: {probe.detail}"

    print(messages.format_dial_in(
        name,
        info,
        state,
        dialin.keys_on_file(spec.host_key_alias, spec.known_hosts),
    ))

    return EXIT_OK


def build_dial_in_block(saved, args) -> dict:
    """One computer's settings, from what was saved and the flags.

    A target that is already there keeps what it has, such as its
    mount place. A port is only kept when the target already dialed
    in: the port of a target the server used to dial is the
    computer's own SSH port, and means something else.
    """

    block = dict(saved) if isinstance(saved, dict) else {}

    if not block.get("dial_in"):
        block.pop("port", None)
        block.pop("host", None)

    if args.user:
        block["user"] = args.user

    if args.server:
        block["dial_in"] = args.server

    if args.port is not None:
        block["port"] = args.port

    return block


def cli_dial_in(runtime: Runtime, args) -> int:
    name = args.target
    changing = args.user or args.server or args.port is not None

    if not changing:
        return show_dial_in(runtime, name, args.keep)

    settings = runtime.raw_settings()
    saved = settings["targets"].get(name)

    # The Hermes server is not a computer that can dial in to itself,
    # and it is often the default. It is not turned into one.
    if isinstance(saved, dict) and saved.get("kind") == KIND_LOCAL:
        print(f"`{name}` is the Hermes server. Give the computer a "
              "name of its own.")
        return EXIT_USAGE

    block = build_dial_in_block(saved, args)

    if not block.get("user") or not block.get("dial_in"):
        print(messages.DIAL_IN_USAGE)
        return EXIT_USAGE

    picked = "port" not in block

    try:
        block = dialin.complete(name, block, settings["targets"])
        settings["targets"][name] = block

        if not settings.get("default"):
            settings["default"] = name

        parse_config(settings)
    except (dialin.DialInError, ConfigError) as error:
        print(error)
        return EXIT_USAGE

    if not args.apply:
        print(messages.format_dial_in_dry(
            MODE_DRY,
            name,
            wizard.render_yaml({"targets": {name: block}}),
            picked,
        ))
        return EXIT_OK

    try:
        runtime.save_settings(settings)
    except RuntimeError as error:
        print(f"{MODE_LIVE} {error}")
        return EXIT_USAGE

    print(f"{MODE_LIVE} written.")
    print()

    return show_dial_in(runtime, name, args.keep)


def mount_targets(config, name: str | None) -> list[TargetSpec]:
    """The targets one `hermes onsite mount` run covers."""

    if name is None:
        return [spec for spec in config.targets.values() if spec.mount]

    if name not in config.targets:
        return []

    spec = config.target(name)

    return [spec] if spec.mount else []


def mount_one(runtime: Runtime, spec: TargetSpec) -> int:
    try:
        found = runtime.mount(spec)
    except mounts.MountError as error:
        print(error)
        return EXIT_DOWN

    start = ""

    try:
        start = str(runtime.start_folder(spec))
    except mounts.MountError:
        # The mount is up and the target is not answering yet. The
        # start folder is known once it does.
        start = ""

    print(messages.format_mounted(spec, str(found.root), start))

    return EXIT_OK


def cli_mount(runtime: Runtime, args) -> int:
    try:
        config = runtime.load_config()
    except ConfigError as error:
        print(error)
        return EXIT_USAGE

    specs = mount_targets(config, args.target)

    if not specs:
        print(messages.NOTHING_TO_MOUNT)
        return EXIT_USAGE

    code = EXIT_OK

    for spec in specs:
        if args.stop:
            runtime.unmount(spec)
            print(f"{spec.label} is no longer mounted.")
            continue

        code = max(code, mount_one(runtime, spec))

    return code


def split_target_folder(value: str) -> tuple[str, str]:
    """`laptop:~/work` as a target and a folder. A bare name is the
    target's own start folder."""

    name, _, folder = value.partition(TARGET_FOLDER_SEPARATOR)

    return name, folder


def cli_open(runtime: Runtime, args) -> int:
    name, target_folder = split_target_folder(args.folder)

    if not name:
        print(messages.OPEN_USAGE)
        return EXIT_USAGE

    try:
        config = runtime.load_config()
    except ConfigError as error:
        print(error)
        return EXIT_USAGE

    if name not in config.targets:
        print(f"`{name}` is not a target.")
        return EXIT_USAGE

    spec = config.target(name)

    try:
        folder, shown = runtime.host_folder(spec, target_folder)
    except mounts.MountError as error:
        print(error)
        return EXIT_DOWN

    print(messages.format_opened(spec, shown, str(folder)))

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


def screen_password() -> str:
    from . import compat

    try:
        return compat.screen_password()
    except Exception:
        # The check still runs. A server that wants a password then
        # says so, with where to set it.
        logger.debug("onsite: no screen password", exc_info=True)

        return ""


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
            f"`{spec.name}` is the Hermes server. Check its screen with "
            "`hermes computer-use doctor`."
        )
        return EXIT_USAGE

    try:
        seat = screen.read_seat(spec)
    except screen.ScreenError as error:
        print(error)
        return EXIT_DOWN

    sharing = screenshare.ask(spec, screen_password())
    route = screen.pick_route(spec.screen, seat)
    problems = screen.find_route_problems(
        spec,
        route,
        seat,
        sharing.problem,
    )

    print(messages.format_screen_report(
        spec,
        seat,
        sharing,
        route,
        problems,
        screen.find_route_notes(route, seat),
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
        "--dial-in",
        action="append",
        default=[],
        metavar="NAME=USER",
        help="a machine this server cannot reach, which connects to "
        "the server by itself. USER is the account on it. Needs "
        "--server. Repeat for more.",
    )
    init.add_argument(
        "--server",
        metavar="USER@ADDRESS[:PORT]",
        help="this server, as a --dial-in machine logs in to it.",
    )
    init.add_argument(
        "--local",
        action="append",
        default=[],
        metavar="NAME",
        help="the Hermes server.",
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
        "--mount",
        action="append",
        default=[],
        metavar="NAME=/HOST/FOLDER",
        help="where on this host NAME's disk is mounted. Default: "
        "~/.local/share/onsite/mount. It must be outside the Hermes "
        "home.",
    )
    init.add_argument(
        "--start",
        action="append",
        default=[],
        metavar="NAME=/TARGET/FOLDER",
        help="the folder on NAME a new session opens in. Default: "
        "the folder a login there lands in.",
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

    dialed = actions.add_parser(
        "dial-in",
        help="add a machine this server cannot reach, which connects "
        "to the server by itself, or show the command it runs. A dry "
        "run unless --apply is given.",
    )
    dialed.add_argument("target", help="a name for the machine.")
    dialed.add_argument(
        "--user",
        help="the account on the machine that tool calls run as.",
    )
    dialed.add_argument(
        "--server",
        metavar="USER@ADDRESS[:PORT]",
        help="this server, as the machine logs in to it.",
    )
    dialed.add_argument(
        "--port",
        type=int,
        help="the port on this server that leads to the machine. "
        "Default: a free one is picked.",
    )
    dialed.add_argument(
        "--keep",
        choices=dialin.SYSTEMS,
        help="show how to keep the link up across restarts of a "
        "machine that runs this system.",
    )
    dialed.add_argument(
        "--apply",
        action="store_true",
        help="write the machine's settings. Without it, they are "
        "shown.",
    )

    mounted = actions.add_parser(
        "mount",
        help="mount a target's disk on this host, so its real files "
        "show in the folder picker and the file tree.",
    )
    mounted.add_argument(
        "target",
        nargs="?",
        help="the target to mount. Default: every target reached "
        "over SSH.",
    )
    mounted.add_argument(
        "--stop",
        action="store_true",
        help="take the mount down. Nothing on the target changes.",
    )

    opened = actions.add_parser(
        "open",
        help="say where a folder on a target is on this host, so a "
        "session can be started in it.",
    )
    opened.add_argument(
        "folder",
        metavar="TARGET[:FOLDER]",
        help="a target, or a folder on one, such as laptop:~/work/site.",
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


def build_cli_handler(ctx, runtime: Runtime):
    def handle_cli(args) -> int:
        action = getattr(args, "onsite_action", None)

        if action == "init":
            return cli_init(runtime, args)

        if action == "dial-in":
            return cli_dial_in(runtime, args)

        if action == "mount":
            return cli_mount(runtime, args)

        if action == "open":
            return cli_open(runtime, args)

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
