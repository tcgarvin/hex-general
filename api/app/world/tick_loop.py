"""A fast turn clock for local playtests: tick one world every N seconds.

Run against the local dev stack (reads ``api/.env`` like the server)::

    uv run python -m app.world.tick_loop --game <world id> --every 300

Each tick is the same `GameService.tick_world` the dev endpoint runs: bots plan,
stored human orders resolve, standing travel continues. Stop with Ctrl-C. A live
world uses the scheduled clock instead (``POST /internal/clock``).
"""

from __future__ import annotations

import argparse
import time

from app.db import get_supabase_client
from app.games.repo import SupabaseGameRepo
from app.games.service import GameService


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", required=True, help="world game id")
    parser.add_argument("--every", type=float, default=300.0, help="seconds per turn")
    parser.add_argument("--turns", type=int, default=0, help="stop after N (0: never)")
    args = parser.parse_args()
    if args.every <= 0:
        parser.error("--every must be positive")

    service = GameService(SupabaseGameRepo(get_supabase_client()))
    done = 0
    while args.turns == 0 or done < args.turns:
        started = time.perf_counter()
        result = service.tick_world(args.game)
        elapsed = time.perf_counter() - started
        done += 1
        print(f"turn {result.turn} ({result.status}) in {elapsed:.1f}s", flush=True)
        if result.status != "active":
            break
        time.sleep(max(0.0, args.every - elapsed))


if __name__ == "__main__":
    main()
