-- Galamsey Watch — security: role helpers, row-level security, audit triggers.
-- The pipeline uses the service-role key, which bypasses RLS by design.
-- Every policy below is for signed-in dashboard users.

-- ---------------------------------------------------------------------------
-- Helpers (security definer so policies can read profiles without recursion)
-- ---------------------------------------------------------------------------
create or replace function app_role() returns user_role
language sql stable security definer set search_path = public as $$
  select role from profiles where user_id = auth.uid()
$$;

create or replace function app_regions() returns text[]
language sql stable security definer set search_path = public as $$
  select coalesce(regions, '{}') from profiles where user_id = auth.uid()
$$;

create or replace function is_staff() returns boolean
language sql stable as $$ select app_role() in ('admin', 'analyst') $$;

create or replace function is_admin() returns boolean
language sql stable as $$ select app_role() = 'admin' $$;

-- New sign-ups get a profile with the lowest role and no regions.
-- An admin must promote them. Nobody can self-promote (see profiles policies).
create or replace function handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into profiles (user_id, full_name) values (new.id, new.raw_user_meta_data->>'full_name');
  return new;
end $$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function handle_new_user();

-- ---------------------------------------------------------------------------
-- Enable RLS everywhere
-- ---------------------------------------------------------------------------
alter table profiles              enable row level security;
alter table reference_layers      enable row level security;
alter table tiles                 enable row level security;
alter table detections            enable row level security;
alter table alerts                enable row level security;
alter table alert_sources         enable row level security;
alter table alert_status_history  enable row level security;
alter table reviews               enable row level security;
alter table field_reports         enable row level security;
alter table tips                  enable row level security;
alter table notifications         enable row level security;
alter table labels                enable row level security;
alter table connectors            enable row level security;
alter table equipment_positions   enable row level security;
alter table enforcement_actions   enable row level security;
alter table pipeline_runs         enable row level security;
alter table settings              enable row level security;

-- Table grants for signed-in users. anon (signed-out) gets nothing until the public map (M11).
grant usage on schema public to authenticated;
grant select on all tables in schema public to authenticated;
grant update on alerts, tips, settings, profiles to authenticated;
grant insert on reviews, field_reports, labels to authenticated;
revoke all on all tables in schema public from anon;

-- profiles: read own; admins read and manage all
create policy profiles_self_read  on profiles for select using (user_id = auth.uid() or is_admin());
create policy profiles_admin_write on profiles for update using (is_admin()) with check (is_admin());

-- reference data and tiles: any signed-in staff or scout may read.
-- Agency layers that are not cleared for sharing are filtered in layer_geojson().
create policy reference_read on reference_layers for select using (app_role() is not null);
create policy tiles_read     on tiles            for select using (app_role() is not null);

-- detections: staff only
create policy detections_read on detections    for select using (is_staff());
create policy alert_sources_read on alert_sources for select using (is_staff());

-- alerts: staff see all; scouts see alerts in their regions
create policy alerts_staff_read on alerts for select using (is_staff());
create policy alerts_scout_read on alerts for select
  using (app_role() = 'field_scout' and region = any(app_regions()));
create policy alerts_staff_update on alerts for update using (is_staff()) with check (is_staff());

create policy history_read on alert_status_history for select
  using (is_staff() or exists (select 1 from alerts a where a.id = alert_id and a.region = any(app_regions())));

-- reviews: staff write their own, staff read
create policy reviews_read   on reviews for select using (is_staff());
create policy reviews_insert on reviews for insert with check (is_staff() and user_id = auth.uid());

-- field reports: scouts insert own and read own; staff read all
create policy field_reports_read on field_reports for select using (is_staff() or user_id = auth.uid());
create policy field_reports_insert on field_reports for insert
  with check (user_id = auth.uid() and app_role() is not null);

-- tips: staff only (tipster identity is already hashed)
create policy tips_read on tips for select using (is_staff());
create policy tips_link on tips for update using (is_staff()) with check (is_staff());

-- labels: staff
create policy labels_read   on labels for select using (is_staff());
create policy labels_insert on labels for insert with check (is_staff() and created_by = auth.uid());

-- admin-only tables
create policy notifications_admin on notifications       for select using (is_admin());
create policy connectors_read     on connectors          for select using (is_staff());
create policy equipment_read      on equipment_positions for select using (is_staff());
create policy enforcement_read    on enforcement_actions for select using (is_staff());
create policy runs_read           on pipeline_runs       for select using (is_staff());
create policy settings_read       on settings            for select using (is_staff());
create policy settings_admin      on settings            for update using (is_admin()) with check (is_admin());

-- ---------------------------------------------------------------------------
-- Alert status flow + audit trail
-- new → reviewing → confirmed | rejected → field_verified → closed
-- (rejected may also close; confirmed may close without a field visit)
-- ---------------------------------------------------------------------------
create or replace function enforce_alert_status() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  allowed boolean;
begin
  if tg_op = 'INSERT' then
    insert into alert_status_history (alert_id, from_status, to_status, changed_by)
      values (new.id, null, new.status, auth.uid());
    return new;
  end if;

  new.updated_at := now();
  if new.status is distinct from old.status then
    allowed := (old.status, new.status) in (
      ('new','reviewing'),
      ('reviewing','confirmed'), ('reviewing','rejected'),
      ('confirmed','field_verified'), ('confirmed','closed'),
      ('rejected','closed'), ('rejected','reviewing'),
      ('field_verified','closed')
    );
    if not allowed then
      raise exception 'Invalid alert status change: % -> %', old.status, new.status;
    end if;
    insert into alert_status_history (alert_id, from_status, to_status, changed_by)
      values (new.id, old.status, new.status, auth.uid());
  end if;
  return new;
end $$;

create trigger alerts_status_insert after insert on alerts
  for each row execute function enforce_alert_status();
create trigger alerts_status_update before update on alerts
  for each row execute function enforce_alert_status();
