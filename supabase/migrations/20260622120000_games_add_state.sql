-- Phase 5 (roadmap): the games row becomes the canonical state container.
-- Per the architecture decision, one games row holds the whole GameState blob
-- (map + units + turn + banks) as JSONB rather than a wide relational schema.
--
-- Lifecycle: a game is created in 'lobby' (one player, waiting for an opponent),
-- flips to 'active' when the second player joins, and 'finished' once a turn
-- resolves into a decided victory. ``scenario`` records which authored scenario
-- the turn-0 state was built from (for display/debugging; the state itself is
-- self-contained). ``state`` is the latest canonical GameState for the game.
--
-- Columns are added with a temporary default so the statement succeeds even if
-- rows already exist, then the default is dropped: new rows must supply state and
-- scenario explicitly (fail loud — there is no sensible default GameState).
alter table public.games
    add column scenario text not null default '',
    add column status text not null default 'lobby',
    add column state jsonb not null default '{}'::jsonb,
    add column updated_at timestamptz not null default now();

alter table public.games alter column scenario drop default;
alter table public.games alter column state drop default;

alter table public.games
    add constraint games_status_check
    check (status in ('lobby', 'active', 'finished'));

comment on column public.games.state is
    'Canonical GameState blob (map + units + turn + banks) for the latest turn.';
comment on column public.games.status is
    'Lifecycle: lobby (awaiting opponent) -> active -> finished (victory decided).';
