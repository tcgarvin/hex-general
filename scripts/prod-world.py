#!/usr/bin/env python3
"""Drive world games on the production API: list, status, create, tick, delete.

The production counterpart of `scripts/game-setup.sh` (which only talks to the
local stack). It calls the live API with the owner's own Supabase access token,
read from the HEX_PROD_TOKEN environment variable, so creating a world no
longer means hand-writing a curl with a JWT. The create, tick, clock and delete
routes are dev-only: the token must belong to an account in the API's
DEV_USER_IDS.

Every command prints the target host first. A write (create, tick, clock,
delete) then asks for a typed confirmation unless --yes is given. The token is
never printed and never passed on a command line (it stays in this process).

Run `python3 scripts/prod-world.py --help` for usage and how to get a token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TextIO
from urllib.parse import urlsplit

DEFAULT_API = "https://hex-api-qfzj5tg56a-uc.a.run.app"
TOKEN_ENV = "HEX_PROD_TOKEN"
API_ENV = "HEX_PROD_API"

READ_TIMEOUT_S = 30
# Generating and storing a 200-slot world takes ~10 s and a tick ~2 s; Cloud
# Run cuts a request off at 300 s, so waiting longer than that is pointless.
WRITE_TIMEOUT_S = 300

WRITES = frozenset({"create", "tick", "clock", "delete"})

TOKEN_HELP = f"""\
getting a token:
  1. Sign in on the production site (https://hex-general.vercel.app) with the
     owner's Google account; it must be listed in the API's DEV_USER_IDS.
  2. Open the browser's developer tools, Application (Chrome) or Storage
     (Firefox) tab, Local Storage, the site's origin. The Supabase client keeps
     the session under the key sb-amhkpsrvjlthmmemwssc-auth-token; its value is
     JSON, and the token is its "access_token" field (a long string of three
     dot-separated parts, starting "eyJ"). Copy only that field.
  3. Put it in the environment without it reaching your shell history, e.g.
       read -rs {TOKEN_ENV} && export {TOKEN_ENV}
     then paste and press Enter (nothing is echoed). The token expires about an
     hour after it was issued; reload the site for a fresh one.

examples:
  python3 scripts/prod-world.py list
  # the agreed first (rehearsal) world: ~60 slots, ~30 bots, scheduled clock
  python3 scripts/prod-world.py create --slots 60 --bots 30 --seed 1
  python3 scripts/prod-world.py status <game_id>
  python3 scripts/prod-world.py tick <game_id>

The target is {DEFAULT_API} unless --api or {API_ENV} says otherwise.
"""


class ToolError(Exception):
    """A refusal or a failed call, reported on stderr with a non-zero exit."""


def build_parser() -> argparse.ArgumentParser:
    """The command-line interface (every subcommand and its arguments)."""
    parser = argparse.ArgumentParser(
        prog="prod-world.py",
        description=(
            "Drive world games on the production API with the owner's token "
            f"(from ${TOKEN_ENV})."
        ),
        epilog=TOKEN_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--api",
        help=f"API base URL (default: ${API_ENV}, else {DEFAULT_API})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the typed confirmation before a write (non-interactive use)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("whoami", help="the token's account and whether it is a dev")
    commands.add_parser("list", help="every running world (GET /worlds)")
    status = commands.add_parser(
        "status", help="one world's turn, clock and next tick (from GET /worlds)"
    )
    status.add_argument("game_id")
    create = commands.add_parser(
        "create", help="generate a world and seat its bots (POST /dev/worlds)"
    )
    create.add_argument("--slots", type=int, required=True, help="slots, 1-400")
    create.add_argument("--bots", type=int, required=True, help="bot-held slots")
    create.add_argument("--seed", type=int, default=0, help="generator seed (0)")
    create.add_argument(
        "--clock",
        choices=("twice_daily", "manual"),
        default="twice_daily",
        help=(
            "twice_daily (default) resolves at 01:00 and 13:00 New York time; "
            "manual only moves when ticked"
        ),
    )
    tick = commands.add_parser(
        "tick",
        help=(
            "resolve a world's turn now (POST /dev/worlds/{id}/tick); a "
            "scheduled world keeps its next scheduled tick"
        ),
    )
    tick.add_argument("game_id")
    commands.add_parser(
        "clock",
        help="run the scheduler's sweep now: resolve every due world (POST /dev/clock)",
    )
    delete = commands.add_parser(
        "delete", help="delete a world and everything stored for it"
    )
    delete.add_argument("game_id")
    return parser


def read_token(env: Mapping[str, str]) -> str:
    """The bearer token from the environment, refused unless it looks like a JWT."""
    token = env.get(TOKEN_ENV, "").strip()
    if not token:
        raise ToolError(f"{TOKEN_ENV} is not set; see --help for how to get a token")
    if (
        token.startswith("{")
        or token.count(".") != 2
        or any(c.isspace() for c in token)
    ):
        raise ToolError(
            f"{TOKEN_ENV} does not look like an access token (three dot-separated "
            "parts); copy only the access_token field, see --help"
        )
    return token


def api_base(cli_value: str | None, env: Mapping[str, str]) -> str:
    """The API base URL (flag, then environment, then the live API), validated."""
    base = (cli_value or env.get(API_ENV) or DEFAULT_API).rstrip("/")
    parts = urlsplit(base)
    local = parts.hostname in ("127.0.0.1", "localhost")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ToolError(f"API URL must be https (http only for localhost): {base}")
    return base


@dataclass(frozen=True)
class Api:
    """The API at ``base``, called with ``token`` as the bearer."""

    base: str
    token: str

    @property
    def host(self) -> str:
        return urlsplit(self.base).netloc

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """Send one request; return the decoded JSON (``None`` for no content).

        Raises `ToolError` with the API's ``detail`` on an HTTP error or with the
        reason when the API can't be reached. The token never appears in it.
        """
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        timeout = READ_TIMEOUT_S if method == "GET" else WRITE_TIMEOUT_S
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ToolError(
                f"{method} {path} -> HTTP {exc.code}: {_detail(exc.read())}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ToolError(
                f"{method} {path}: cannot reach {self.host}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise ToolError(
                f"{method} {path}: no answer from {self.host} in {timeout} s; a write "
                "may still have happened, check with list or status"
            ) from exc
        return json.loads(raw) if raw else None


def _detail(raw: bytes) -> str:
    """The ``detail`` of an API error body, or the body itself if it has none."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")[:500] or "<empty body>"
    if isinstance(parsed, dict) and "detail" in parsed:
        return str(parsed["detail"])
    return json.dumps(parsed)[:500]


def ask(prompt: str) -> str:
    """Read one line from stdin, prompting on stderr (stdout carries the JSON)."""
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline()


def confirm(
    prompt: str, expected: str, assume_yes: bool, read: Callable[[str], str]
) -> None:
    """Refuse unless the user types ``expected`` (or ``--yes`` was given)."""
    if assume_yes:
        return
    answer = read(f'{prompt}\nType "{expected}" to continue: ')
    if answer.strip() != expected:
        raise ToolError("not confirmed; nothing was sent")


def _world(api: Api, game_id: str) -> dict[str, Any]:
    """The `GET /worlds` row of ``game_id``; refused if it is not running."""
    for row in api.call("GET", "/worlds"):
        if row["game_id"] == game_id:
            return dict(row)
    raise ToolError(f"no running world {game_id} (unknown, deleted or finished)")


def _status(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A world row plus how long until its next tick."""
    status = dict(row)
    due = row["next_tick_at"]
    if due is None:
        status["next_tick_in"] = "not scheduled (manual clock)"
    else:
        left = datetime.fromisoformat(due) - now
        minutes = int(left.total_seconds() // 60)
        status["next_tick_in"] = (
            "due now (waiting for the clock)" if minutes < 0 else f"{minutes} min"
        )
    return status


def run(
    args: argparse.Namespace,
    api: Api,
    read: Callable[[str], str],
    err: TextIO,
    now: datetime,
) -> Any:
    """Carry out one parsed command against ``api``; return what to print."""
    command = args.command
    if command == "whoami":
        return api.call("GET", "/me")
    if command == "list":
        return api.call("GET", "/worlds")
    if command == "status":
        return _status(_world(api, args.game_id), now)
    if command == "create":
        body = {
            "slots": args.slots,
            "bots": args.bots,
            "seed": args.seed,
            "clock": args.clock,
        }
        confirm(
            f"Create a world on {api.host}: {json.dumps(body)}",
            "create",
            args.yes,
            read,
        )
        created = api.call("POST", "/dev/worlds", body)
        print(f"created world {created['game_id']} on {api.host}", file=err)
        return created
    if command == "tick":
        row = _world(api, args.game_id)
        confirm(
            f"Resolve turn {row['turn']} of world {args.game_id} on {api.host} "
            f"now ({row['clock']} clock)",
            "tick",
            args.yes,
            read,
        )
        return api.call("POST", f"/dev/worlds/{args.game_id}/tick")
    if command == "clock":
        confirm(f"Resolve every due world on {api.host} now", "clock", args.yes, read)
        return api.call("POST", "/dev/clock")
    if command == "delete":
        confirm(
            f"Delete world {args.game_id} on {api.host} with its players, orders, "
            "turns and map. This cannot be undone",
            args.game_id,
            args.yes,
            read,
        )
        api.call("DELETE", f"/dev/worlds/{args.game_id}")
        print(f"deleted world {args.game_id} on {api.host}", file=err)
        return None
    raise AssertionError(f"unhandled command {command}")  # argparse restricts it


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] = os.environ,
    read: Callable[[str], str] = ask,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Parse ``argv``, run the command and print its JSON; return the exit code."""
    args = build_parser().parse_args(argv)
    try:
        api = Api(base=api_base(args.api, env), token=read_token(env))
        print(f"target: {api.host}", file=err)
        result = run(args, api, read, err, datetime.now(timezone.utc))
    except ToolError as exc:
        print(f"prod-world: {exc}", file=err)
        return 1
    if result is not None:
        print(json.dumps(result, indent=2), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
