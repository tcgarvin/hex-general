-- AI Phase 4 (AI_SPEC §9): "vs bot" games.
--
-- A bot opponent occupies a normal players row so the whole turn lifecycle
-- (submission storage, the all-locked resolve trigger, resign/victory) treats it
-- exactly like a human — no trust shortcut. The row's ``user_id`` is a freshly
-- generated UUID that corresponds to no Supabase Auth user (it can never log in),
-- and ``bot_policy`` marks the row as machine-driven and names the AI policy
-- (resolved through app.ai.arena's registry) the service runs for the slot.
-- NULL means a human player — the one, well-defined meaning.
alter table public.players add column bot_policy text;

comment on column public.players.bot_policy is
    'AI policy name driving this slot (app.ai.arena registry); NULL = human.';
