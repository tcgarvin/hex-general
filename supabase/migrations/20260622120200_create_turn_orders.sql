-- Phase 5 (roadmap): a player's locked order submission for one turn.
--
-- Each row is one player's complete plan for one turn: the ``orders`` JSONB holds
-- the per-unit Order list plus any Recruitment requests (validated server-side at
-- submit time). In v0.1 submitting a turn locks it (one row per player per turn,
-- enforced by the unique constraint) — there is no separate draft state. When
-- every player in a game has a row for the current turn, the API resolves the
-- turn with the pure engine.
--
-- Only the API (service-role key) touches this table; RLS enabled, no policies.
create table public.turn_orders (
    id uuid primary key default gen_random_uuid(),
    game_id uuid not null references public.games (id) on delete cascade,
    player_id uuid not null references public.players (id) on delete cascade,
    turn int not null,
    orders jsonb not null,
    locked boolean not null default true,
    submitted_at timestamptz not null default now(),
    unique (game_id, player_id, turn)
);

comment on table public.turn_orders is
    'One player''s locked order + recruitment submission for one turn (JSONB plan).';

create index turn_orders_game_turn_idx on public.turn_orders (game_id, turn);

alter table public.turn_orders enable row level security;

grant select, insert, update, delete on table public.turn_orders to service_role;
