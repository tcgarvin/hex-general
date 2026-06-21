from datetime import datetime, timedelta, timezone

from postgrest import CountMethod
from supabase import Client

# A session counts as "active" if it was seen within this window.
ACTIVE_WINDOW = timedelta(minutes=5)


def count_active_users(client: Client) -> int:
    """Return the number of sessions seen within ACTIVE_WINDOW.

    Queries the ``active_users`` table for an exact row count restricted to
    recent ``last_seen_at`` values. Raises if Supabase returns no count rather
    than silently reporting zero, so a broken query surfaces as an error.
    """
    cutoff = (datetime.now(timezone.utc) - ACTIVE_WINDOW).isoformat()
    response = (
        client.table("active_users")
        .select("session_id", count=CountMethod.exact)
        .gte("last_seen_at", cutoff)
        .execute()
    )
    count = response.count
    if count is None:
        raise RuntimeError("Supabase returned no count for active_users")
    return count


def count_games(client: Client) -> int:
    """Return the total number of game instances.

    Queries the ``games`` table for an exact row count. Raises if Supabase
    returns no count rather than silently reporting zero, so a broken query
    surfaces as an error.
    """
    response = client.table("games").select("id", count=CountMethod.exact).execute()
    count = response.count
    if count is None:
        raise RuntimeError("Supabase returned no count for games")
    return count
