-- The service-role key (used only by the API) bypasses RLS but still needs
-- table-level privileges. Grant full DML to service_role so the API can read
-- the active-user count and later upsert sessions. RLS stays enabled with no
-- policies, so anon/authenticated (the browser's publishable key) remain
-- denied -- the table is reachable only through the API.
grant select, insert, update, delete on table public.active_users to service_role;
