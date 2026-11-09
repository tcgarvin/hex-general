-- World sites (economy draft): each settlement's kind and garrison tier, plus every
-- slot's base site, stored once with the map as a list of
-- {"coord": {"q", "r"}, "kind", "garrison"} objects. Validated by the API's
-- CompactMap on read.
alter table public.game_maps
  add column sites jsonb not null default '[]'::jsonb;
