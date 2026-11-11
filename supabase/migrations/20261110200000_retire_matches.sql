-- WORLD_SPEC §10.1: the server runs only world games; the 2-player match retires.
--
-- A match was a games row with its map embedded in the state (map_id NULL), an
-- invite link (invite_token) to share its open slot, and no clock. Every game is
-- now a world: its map is stored once in game_maps and its slots are filled by
-- joining (POST /worlds/{id}/join), so there is no invite secret to guard.
--
-- We are pre-launch, so the remaining matches are disposable: delete them rather
-- than convert them. Their players, turn_orders and turns rows go with them
-- (on delete cascade from games).
delete from public.games where map_id is null;

-- Every game now stores its map apart; the API trusts this and no longer reads a
-- map embedded in games.state or turns.state_after.
alter table public.games
    alter column map_id set not null;

comment on column public.games.map_id is
    'Shared static map (game_maps); games.state and turns.state_after omit the map.';

-- Joining a world needs no secret, and nothing reads or writes the token any more.
alter table public.games
    drop column invite_token;
