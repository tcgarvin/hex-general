"""A fast turn clock for local playtests: tick one world every N seconds.

Run against the local dev stack (reads ``api/.env`` like the server)::

    uv run python -m app.world.tick_loop --game <world id> --every 300

Each tick is the same `GameService.tick_world` the dev endpoint runs: bots plan,
stored human orders resolve, standing travel continues. The loop waits a full
interval before its first tick, so players can plan the current turn, and
publishes each upcoming tick time on the world so the UI shows a countdown; it
clears that time when it stops (Ctrl-C). A live world uses the scheduled clock
instead (``POST /internal/clock``).
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

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
    every = timedelta(seconds=args.every)
    due = datetime.now(timezone.utc) + every
    service.announce_tick(args.game, due)
    print(f"first tick at {due:%H:%M:%S} UTC", flush=True)
    done = 0
    try:
        while args.turns == 0 or done < args.turns:
            time.sleep(max(0.0, (due - datetime.now(timezone.utc)).total_seconds()))
            started = time.perf_counter()
            last = args.turns != 0 and done + 1 >= args.turns
            due = datetime.now(timezone.utc) + every
            result = service.tick_world(args.game, next_tick_at=None if last else due)
            done += 1
            elapsed = time.perf_counter() - started
            print(
                f"turn {result.turn} ({result.status}) in {elapsed:.1f}s",
                flush=True,
            )
            if result.status != "active":
                break
    finally:
        service.announce_tick(args.game, None)


if __name__ == "__main__":
    main()
