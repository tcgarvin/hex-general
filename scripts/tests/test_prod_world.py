"""scripts/prod-world.py: token and confirmation handling against a stub API."""

import importlib.util
import io
import json
import sys
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "prod-world.py"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJvd25lciJ9.c2lnbmF0dXJl"
WORLD = {
    "game_id": "11111111-2222-3333-4444-555555555555",
    "turn": 4,
    "slots": 200,
    "players": 101,
    "humans": 1,
    "clock": "twice_daily",
    "next_tick_at": "2026-11-12T06:00:00Z",
}


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prod_world", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["prod_world"] = module
    spec.loader.exec_module(module)
    return module


pw = _load()


class _Stub(BaseHTTPRequestHandler):
    """Answers like the API's world routes and records every request."""

    seen: list[dict] = []

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.seen.append(
            {
                "method": self.command,
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            }
        )
        routes = {
            ("GET", "/worlds"): (200, [WORLD]),
            ("GET", "/me"): (200, {"user_id": "owner", "is_dev": True}),
            ("POST", "/dev/worlds"): (201, {"game_id": "new-world"}),
            ("POST", f"/dev/worlds/{WORLD['game_id']}/tick"): (
                200,
                {"locked": True, "executed": True, "turn": 5, "status": "active"},
            ),
            ("POST", "/dev/clock"): (200, {"ticked": [], "skipped": []}),
            ("DELETE", f"/dev/worlds/{WORLD['game_id']}"): (204, None),
        }
        status, payload = routes.get(
            (self.command, self.path), (403, {"detail": "dev-only endpoint"})
        )
        raw = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_DELETE = _answer

    def log_message(self, *_: object) -> None:  # keep test output quiet
        pass


@pytest.fixture
def stub() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    _Stub.seen = []
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _run(
    argv: list[str], env: dict[str, str], answers: list[str] | None = None
) -> tuple[int, str, str, list[str]]:
    """Run the tool; return its exit code, stdout, stderr and the prompts it showed."""
    out, err = io.StringIO(), io.StringIO()
    prompts: list[str] = []
    pending = list(answers or [])

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return pending.pop(0) if pending else ""

    code = pw.main(argv, env=env, read=read, out=out, err=err)
    return code, out.getvalue(), err.getvalue(), prompts


def _env(stub: str) -> dict[str, str]:
    return {"HEX_PROD_TOKEN": TOKEN, "HEX_PROD_API": stub}


def test_it_refuses_to_run_without_a_token(stub: str) -> None:
    code, _, err, _ = _run(["list"], {"HEX_PROD_API": stub})
    assert code == 1 and "HEX_PROD_TOKEN is not set" in err
    assert _Stub.seen == []


@pytest.mark.parametrize(
    "token", ['{"access_token": "x.y.z"}', "not-a-jwt", "a.b.c d", "a.b"]
)
def test_it_refuses_a_value_that_is_not_an_access_token(stub: str, token: str) -> None:
    code, _, err, _ = _run(["list"], {"HEX_PROD_TOKEN": token, "HEX_PROD_API": stub})
    assert code == 1 and "does not look like an access token" in err
    assert _Stub.seen == []


def test_it_refuses_a_plain_http_api_that_is_not_local() -> None:
    env = {"HEX_PROD_TOKEN": TOKEN, "HEX_PROD_API": "http://example.com"}
    code, _, err, _ = _run(["list"], env)
    assert code == 1 and "must be https" in err


def test_list_sends_the_token_as_a_bearer_and_prints_the_worlds(stub: str) -> None:
    code, out, err, _ = _run(["list"], _env(stub))
    assert code == 0
    assert json.loads(out) == [WORLD]
    assert err.startswith("target: 127.0.0.1:")
    assert _Stub.seen == [
        {"method": "GET", "path": "/worlds", "auth": f"Bearer {TOKEN}", "body": None}
    ]
    assert TOKEN not in out + err


def test_status_reports_the_world_and_its_next_tick(stub: str) -> None:
    status = pw._status(WORLD, datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc))
    assert status["next_tick_in"] == "60 min"
    code, out, _, _ = _run(["status", WORLD["game_id"]], _env(stub))
    assert code == 0 and json.loads(out)["turn"] == 4


def test_status_of_a_world_that_is_not_running_fails(stub: str) -> None:
    code, _, err, _ = _run(["status", "no-such-world"], _env(stub))
    assert code == 1 and "no running world no-such-world" in err


def test_create_without_the_typed_confirmation_sends_nothing(stub: str) -> None:
    argv = ["create", "--slots", "200", "--bots", "100", "--seed", "7"]
    code, _, err, prompts = _run(argv, _env(stub), answers=["yes"])
    assert code == 1 and "not confirmed" in err
    assert 'Type "create"' in prompts[0] and "127.0.0.1" in prompts[0]
    assert all(r["method"] == "GET" for r in _Stub.seen)


def test_create_after_confirmation_posts_the_world_and_names_the_host_first(
    stub: str,
) -> None:
    argv = ["create", "--slots", "200", "--bots", "100", "--seed", "7"]
    code, out, err, _ = _run(argv, _env(stub), answers=["create"])
    assert code == 0 and json.loads(out) == {"game_id": "new-world"}
    assert err.index("target: 127.0.0.1") < err.index("created world new-world")
    assert _Stub.seen[-1]["body"] == {
        "slots": 200,
        "bots": 100,
        "seed": 7,
        "clock": "twice_daily",
    }


def test_yes_skips_the_confirmation_but_still_names_the_host(stub: str) -> None:
    code, _, err, prompts = _run(
        ["--yes", "create", "--slots", "8", "--bots", "4", "--clock", "manual"],
        _env(stub),
    )
    assert code == 0 and prompts == [] and "target: 127.0.0.1" in err
    assert _Stub.seen[-1]["body"]["clock"] == "manual"


def test_tick_confirms_with_the_turn_it_will_resolve(stub: str) -> None:
    code, out, _, prompts = _run(
        ["tick", WORLD["game_id"]], _env(stub), answers=["tick"]
    )
    assert code == 0 and json.loads(out)["turn"] == 5
    assert "Resolve turn 4" in prompts[0]
    assert _Stub.seen[-1]["path"] == f"/dev/worlds/{WORLD['game_id']}/tick"


def test_delete_needs_the_world_id_typed_back(stub: str) -> None:
    game_id = WORLD["game_id"]
    code, _, _, _ = _run(["delete", game_id], _env(stub), answers=["delete"])
    assert code == 1 and all(r["method"] != "DELETE" for r in _Stub.seen)
    code, _, err, _ = _run(["delete", game_id], _env(stub), answers=[game_id])
    assert code == 0 and f"deleted world {game_id}" in err
    assert _Stub.seen[-1]["method"] == "DELETE"


def test_the_clock_sweep_posts_after_confirmation(stub: str) -> None:
    code, out, _, _ = _run(["clock"], _env(stub), answers=["clock"])
    assert code == 0 and json.loads(out) == {"ticked": [], "skipped": []}
    assert _Stub.seen[-1]["path"] == "/dev/clock"


def test_an_api_error_is_reported_with_its_detail_and_without_the_token(
    stub: str,
) -> None:
    api = pw.Api(base=stub, token=TOKEN)
    with pytest.raises(pw.ToolError, match="HTTP 403: dev-only endpoint") as caught:
        api.call("POST", "/dev/worlds/other/tick")
    assert TOKEN not in str(caught.value)


def test_a_missing_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        pw.main([], env={"HEX_PROD_TOKEN": TOKEN})
    assert caught.value.code == 2
