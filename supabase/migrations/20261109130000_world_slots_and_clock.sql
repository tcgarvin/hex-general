-- WORLD_SPEC §10.1 (W5): what a running world needs beyond its map.
--
-- game_maps.slots: the generated world's capitals in fill order, as a JSON list
-- of axial {q, r}. The slot at fill position k is player "s<k+1, 3 digits>"
-- (app.world.seating.slot_id). World creation seats the bots in the first slots;
-- a player who joins later takes the first slot that is still open.
alter table public.game_maps
    add column slots jsonb not null default '[]'::jsonb;

comment on column public.game_maps.slots is
    'Capitals in fill order ([{q, r}, ...]); fill position k is slot s<k+1>.';

-- games.clock: how turns advance. "manual" resolves only when a dev ticks the
-- world; "twice_daily" resolves at next_tick_at (01:00 and 13:00
-- America/New_York, WORLD_SPEC §3), driven by Cloud Scheduler. next_tick_at is
-- the next scheduled resolution; NULL means none is scheduled (a manual clock).
alter table public.games
    add column clock text not null default 'manual',
    add column next_tick_at timestamptz;

alter table public.games
    add constraint games_clock_check check (clock in ('manual', 'twice_daily'));

comment on column public.games.clock is
    'How turns advance: manual (dev tick) or twice_daily (scheduled at next_tick_at).';
comment on column public.games.next_tick_at is
    'Next scheduled resolution for a twice_daily world; NULL = none scheduled.';
