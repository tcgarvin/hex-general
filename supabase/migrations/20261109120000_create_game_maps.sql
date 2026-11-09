-- WORLD_SPEC §10.1 (W5): store a world's static map once, not in every state row.
--
-- A 200-slot world map is ~10^5 hexes: ~7 MB as the GameState hex list, so
-- embedding it in games.state and every turns.state_after row would cost ~14 MB
-- per turn. Instead the map is stored once here, in the compact grid encoding
-- (app.engine.map_codec.CompactMap: one terrain char and one road char per hex,
-- row-major over the odd-q offset rectangle, ~250 KB for 349x349 before TOAST
-- compression).
--
-- ``params`` is the world generator's WorldParams, so the map can be
-- regenerated; NULL means the map was not made by the generator.
--
-- Only the API (service-role key) touches this table; RLS enabled, no policies.
create table public.game_maps (
    id uuid primary key default gen_random_uuid(),
    width int not null check (width > 0),
    height int not null check (height > 0),
    terrain text not null,
    roads text not null,
    params jsonb,
    created_at timestamptz not null default now(),
    check (length(terrain) = width * height),
    check (length(roads) = width * height)
);

comment on table public.game_maps is
    'Static world maps in the compact grid encoding; one row shared by a game''s states.';

alter table public.game_maps enable row level security;

grant select, insert, update, delete on table public.game_maps to service_role;

-- A game with a map_id stores its state *without* the map: games.state and
-- turns.state_after omit the "map" key, and the API rejoins the stored map on
-- load. NULL means the map is embedded in the state (v0.1 matches).
alter table public.games
    add column map_id uuid references public.game_maps (id);

comment on column public.games.map_id is
    'Shared static map (game_maps); NULL = the map is embedded in state (v0.1 match).';

create index games_map_id_idx on public.games (map_id);
