-- Phase 5 (roadmap): player identity for async two-player play.
--
-- A players row binds an opaque bearer ``token`` to one scenario slot (the
-- GameState PlayerId, e.g. 'blue'/'red') in one game. The token is the
-- lightweight identity for v0.1 (full auth deferred): a client presents it in
-- the X-Player-Token header and the API resolves which game + slot it owns, so
-- a player can only submit orders for their own units.
--
-- One slot per game is claimed per row (unique (game_id, slot)); a game is full
-- when every scenario slot has a players row. Only the API (service-role key)
-- touches this table, so RLS is enabled with no policies (all anon/authenticated
-- access via the publishable key is denied) — the token must never reach the
-- browser of the opposing player.
create table public.players (
    id uuid primary key default gen_random_uuid(),
    game_id uuid not null references public.games (id) on delete cascade,
    slot text not null,
    token text not null unique,
    created_at timestamptz not null default now(),
    unique (game_id, slot)
);

comment on table public.players is
    'Player identity: binds an opaque bearer token to one scenario slot in one game.';

create index players_game_id_idx on public.players (game_id);

alter table public.players enable row level security;

-- service_role bypasses RLS but still needs table-level privileges, which a
-- migration-created table does not get by default. Grant full DML.
grant select, insert, update, delete on table public.players to service_role;
