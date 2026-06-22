-- Phase 5 (roadmap): the resolved record of one turn — the replay.
--
-- When all players' orders are in, the engine resolves the turn and the result
-- is recorded here: ``events`` is the ordered Event log (the replay the frontend
-- steps through, SPEC §16.2), ``state_after`` is the resulting GameState, and
-- ``victory`` is the VictoryStatus snapshot evaluated against that state. ``turn``
-- is the turn number that was resolved (the planning turn the orders belonged to).
--
-- This table is append-only game history; games.state always mirrors the latest
-- row's state_after. Only the API (service-role key) touches it; RLS enabled.
create table public.turns (
    id uuid primary key default gen_random_uuid(),
    game_id uuid not null references public.games (id) on delete cascade,
    turn int not null,
    events jsonb not null,
    state_after jsonb not null,
    victory jsonb not null,
    resolved_at timestamptz not null default now(),
    unique (game_id, turn)
);

comment on table public.turns is
    'Resolved turn history: event log (replay) + resulting state + victory snapshot.';

create index turns_game_id_idx on public.turns (game_id);

alter table public.turns enable row level security;

grant select, insert, update, delete on table public.turns to service_role;
