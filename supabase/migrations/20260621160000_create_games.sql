-- A game instance: one match of the hex wargame. v0.1 keeps this minimal --
-- just enough for the entity to exist and be counted. Turn state, players,
-- and the map live in later migrations as the execution engine grows.
--
-- Only the API (service-role key) touches this table, so RLS is enabled with
-- no policies, denying all anon/authenticated access via the publishable key.
-- The browser reaches game data only through the Cloud Run API.
create table public.games (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    created_at timestamptz not null default now()
);

comment on table public.games is
    'Game instances (one match each); read/written only by the API via the service-role key.';

alter table public.games enable row level security;

-- service_role bypasses RLS but still needs table-level privileges, which a
-- migration-created table does not get by default (the API would 500 with
-- "permission denied for table games" otherwise). Grant full DML.
grant select, insert, update, delete on table public.games to service_role;
