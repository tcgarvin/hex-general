-- Tracks currently-active user sessions for the live "active users" stat.
-- A session upserts its row on activity; a row is considered "active" when
-- last_seen_at is recent. Only the API (service-role key) reads/writes this
-- table, so RLS is enabled with no policies -> all anon/authenticated access
-- via the publishable key is denied. The browser never touches it directly;
-- it goes through the Cloud Run API.
create table public.active_users (
    session_id text primary key,
    last_seen_at timestamptz not null default now()
);

comment on table public.active_users is
    'Active user sessions; read/written only by the API via the service-role key.';

create index active_users_last_seen_at_idx
    on public.active_users (last_seen_at);

alter table public.active_users enable row level security;
