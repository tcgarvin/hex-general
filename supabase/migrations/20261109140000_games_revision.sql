-- WORLD_SPEC §10.1 (W5): optimistic concurrency on games.state.
--
-- A world's state is written by joins and by the tick, which can overlap (a
-- tick plans and resolves for ~1-2 s). Every state write is a compare-and-set
-- on ``revision``: the API updates ``where revision = <the revision it read>``
-- and bumps it, so a writer that read a stale state matches no row and retries
-- from a fresh read, instead of silently overwriting the other write.
alter table public.games
    add column revision bigint not null default 0;

comment on column public.games.revision is
    'Bumped on every state write; writes compare-and-set on it (optimistic concurrency).';
