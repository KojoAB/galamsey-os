-- Galamsey Watch — Milestones 2–12: notifications, review workflow, tips, storage,
-- stats, public map, connectors, paid-data controls.

-- ---------------------------------------------------------------------------
-- Extra columns
-- ---------------------------------------------------------------------------
alter table alerts
  add column chips          jsonb not null default '{}',     -- {"s2_true_before": "chips/123/...png", ...}
  add column sources        text[] not null default '{}',    -- distinct detection sources, for filtering
  add column ai_probability numeric(4,3),
  add column notified_at    timestamptz,
  add column digest_only    boolean not null default false;  -- held back by the daily cap
create index alerts_sources_idx on alerts using gin (sources);

alter table tips add column status text not null default 'new' check (status in ('new','checked','linked','spam'));
alter table tips add column last_message_at timestamptz not null default now();

-- ---------------------------------------------------------------------------
-- Alert recipients per region (M3)
-- ---------------------------------------------------------------------------
create table recipients (
  id         bigint generated always as identity primary key,
  name       text not null,
  region     text,                                   -- null = national (all regions)
  channel    text not null check (channel in ('whatsapp', 'sms', 'email')),
  address    text not null,                          -- E.164 phone or email
  levels     alert_level[] not null default '{red,amber}',
  weekly_digest boolean not null default true,
  active     boolean not null default true,
  created_at timestamptz not null default now(),
  unique (channel, address, region)
);
alter table recipients enable row level security;
create policy recipients_admin_read  on recipients for select using (is_admin());
create policy recipients_admin_write on recipients for all using (is_admin()) with check (is_admin());
grant select, insert, update, delete on recipients to authenticated;

-- ---------------------------------------------------------------------------
-- Water-quality readings from WRC (M10)
-- ---------------------------------------------------------------------------
create table water_quality_readings (
  id            bigint generated always as identity primary key,
  connector_id  text not null references connectors(id),
  station_id    text not null,
  station_name  text,
  geom          geometry(Point, 4326) not null,
  measured_at   timestamptz not null,
  turbidity_ntu numeric,
  extra         jsonb not null default '{}',
  unique (connector_id, station_id, measured_at)
);
create index wq_geom_idx on water_quality_readings using gist (geom);
alter table water_quality_readings enable row level security;
create policy wq_read on water_quality_readings for select using (is_staff());

-- ---------------------------------------------------------------------------
-- Paid data (M12): tasking requests need admin approval; spend is tracked
-- ---------------------------------------------------------------------------
create table tasking_requests (
  id           bigint generated always as identity primary key,
  alert_id     bigint not null references alerts(id) on delete cascade,
  aoi          geometry(Polygon, 4326) not null,
  priority     text not null default 'standard' check (priority in ('standard', 'urgent')),
  provider     text not null default 'planet',
  status       text not null default 'pending_approval'
               check (status in ('pending_approval', 'approved', 'rejected', 'submitted', 'delivered')),
  estimated_cost_usd numeric(10,2),
  requested_by uuid not null references auth.users(id),
  approved_by  uuid references auth.users(id),
  notes        text,
  created_at   timestamptz not null default now(),
  decided_at   timestamptz
);
create table paid_spend (
  id          bigint generated always as identity primary key,
  provider    text not null,
  description text not null,
  amount_usd  numeric(10,2) not null check (amount_usd >= 0),
  spent_on    date not null default current_date,
  reference   text
);
alter table tasking_requests enable row level security;
alter table paid_spend enable row level security;
create policy tasking_read   on tasking_requests for select using (is_staff());
create policy tasking_insert on tasking_requests for insert
  with check (is_staff() and requested_by = auth.uid() and status = 'pending_approval');
create policy tasking_admin  on tasking_requests for update using (is_admin()) with check (is_admin());
create policy spend_read on paid_spend for select using (is_admin());
grant select, insert, update on tasking_requests to authenticated;
grant select on paid_spend, water_quality_readings to authenticated;

-- ---------------------------------------------------------------------------
-- Tip-line rate limiting (M7). Counts per hashed sender per hour.
-- ---------------------------------------------------------------------------
create table tip_rate (
  phone_hash text not null,
  hour       timestamptz not null,
  count      integer not null default 0,
  primary key (phone_hash, hour)
);
alter table tip_rate enable row level security;   -- no policies: service role only

create or replace function tip_rate_hit(p_hash text, p_limit integer)
returns boolean language plpgsql security definer set search_path = public as $$
declare c integer;
begin
  insert into tip_rate (phone_hash, hour, count) values (p_hash, date_trunc('hour', now()), 1)
  on conflict (phone_hash, hour) do update set count = tip_rate.count + 1
  returning count into c;
  delete from tip_rate where hour < now() - interval '2 days';
  return c <= p_limit;          -- true = allowed
end $$;
revoke execute on function tip_rate_hit(text, integer) from public, anon, authenticated;

-- Link a tip to the nearest alert within 2 km, else leave it as a tip-only item.
create or replace function link_tip(p_tip bigint)
returns bigint language plpgsql security definer set search_path = public as $$
declare a bigint;
begin
  select al.id into a
  from tips t join alerts al
    on st_dwithin(al.geom::geography, t.geom::geography, 2000)
  where t.id = p_tip and t.geom is not null
  order by al.geom::geography <-> t.geom::geography
  limit 1;
  if a is not null then
    update tips set linked_alert_id = a, status = 'linked' where id = p_tip;
  end if;
  return a;
end $$;
revoke execute on function link_tip(bigint) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Review workflow (M6): one call records the review, moves the status and
-- stores the decision as a training label (M8).
-- ---------------------------------------------------------------------------
create or replace function review_alert(p_alert bigint, p_decision review_decision, p_notes text)
returns alert_status language plpgsql security invoker set search_path = public as $$
declare cur alert_status;
begin
  if not is_staff() then raise exception 'Only analysts and admins can review alerts'; end if;
  select status into cur from alerts where id = p_alert for update;
  if cur is null then raise exception 'Alert % not found', p_alert; end if;

  insert into reviews (alert_id, user_id, decision, notes) values (p_alert, auth.uid(), p_decision, p_notes);

  if cur = 'new' then
    update alerts set status = 'reviewing' where id = p_alert;
    cur := 'reviewing';
  end if;
  if p_decision = 'confirm' and cur = 'reviewing' then
    update alerts set status = 'confirmed' where id = p_alert;
  elsif p_decision = 'reject' and cur = 'reviewing' then
    update alerts set status = 'rejected' where id = p_alert;
  end if;

  if p_decision in ('confirm', 'reject') then
    insert into labels (geom, label, image_date, alert_id, created_by)
    select geom, case when p_decision = 'confirm' then 'mine' else 'not_mine' end, last_seen, id, auth.uid()
    from alerts where id = p_alert;
  end if;
  return (select status from alerts where id = p_alert);
end $$;
grant execute on function review_alert(bigint, review_decision, text) to authenticated;

-- Scouts may mark their own assigned, confirmed alert as field-verified.
create policy alerts_scout_verify on alerts for update
  using (app_role() = 'field_scout' and assigned_to = auth.uid() and status = 'confirmed')
  with check (assigned_to = auth.uid() and status in ('confirmed', 'field_verified'));
grant insert, update on enforcement_actions to authenticated;
create policy enforcement_write on enforcement_actions for insert with check (is_staff());

-- ---------------------------------------------------------------------------
-- Map and list RPCs
-- ---------------------------------------------------------------------------
create or replace function alerts_geojson(
  p_from date default current_date - 30, p_to date default current_date,
  p_levels alert_level[] default null, p_statuses alert_status[] default null,
  p_region text default null, p_source text default null)
returns json language sql stable security invoker set search_path = public as $$
  select json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
    'type', 'Feature', 'id', id,
    'geometry', st_asgeojson(geom, 6)::json,
    'properties', json_build_object('id', id, 'level', level, 'score', score, 'status', status,
      'area_ha', area_ha, 'region', region, 'district', district, 'first_seen', first_seen,
      'sources', sources, 'in_forest_reserve', in_forest_reserve, 'river_distance_m', river_distance_m)
  ) order by score), '[]'::json))
  from alerts
  where last_seen between p_from and p_to
    and (p_levels is null or level = any(p_levels))
    and (p_statuses is null or status = any(p_statuses))
    and (p_region is null or region = p_region)
    and (p_source is null or p_source = any(sources))
$$;

create or replace function tips_geojson()
returns json language sql stable security invoker set search_path = public as $$
  select json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
    'type', 'Feature', 'id', id, 'geometry', st_asgeojson(geom, 5)::json,
    'properties', json_build_object('id', id, 'reference_code', reference_code, 'status', status,
                                    'received_at', received_at, 'linked_alert_id', linked_alert_id)
  )), '[]'::json))
  from tips where geom is not null
$$;
grant execute on function alerts_geojson(date, date, alert_level[], alert_status[], text, text) to authenticated;
grant execute on function tips_geojson() to authenticated;

-- ---------------------------------------------------------------------------
-- Stats (M9). security invoker: users only count what RLS lets them see.
-- ---------------------------------------------------------------------------
create or replace view stats_weekly_hectares with (security_invoker = true) as
  select date_trunc('week', first_seen)::date as week, coalesce(region, 'Unknown') as region,
         round(sum(area_ha), 2) as hectares, count(*) as alerts
  from alerts where status not in ('rejected')
  group by 1, 2;

create or replace view stats_accuracy with (security_invoker = true) as
  select date_trunc('month', first_seen)::date as month,
         count(*) filter (where status in ('confirmed', 'field_verified', 'closed') and
                          exists (select 1 from reviews r where r.alert_id = alerts.id and r.decision = 'confirm')) as confirmed,
         count(*) filter (where status = 'rejected' or
                          exists (select 1 from reviews r where r.alert_id = alerts.id and r.decision = 'reject')) as rejected
  from alerts group by 1;

create or replace view stats_rivers with (security_invoker = true) as
  select river_name, count(*) as alerts, round(sum(area_ha), 2) as hectares, max(last_seen) as latest
  from alerts
  where river_name is not null and river_distance_m <= 500 and status in ('confirmed', 'field_verified', 'closed')
  group by river_name;

-- Hours from alert creation to its first review decision.
create or replace view stats_response_time with (security_invoker = true) as
  select date_trunc('week', a.created_at)::date as week,
         round(percentile_cont(0.5) within group (order by extract(epoch from (h.changed_at - a.created_at)) / 3600)::numeric, 1) as median_hours,
         count(*) as reviewed
  from alerts a
  join lateral (select min(changed_at) as changed_at from alert_status_history
                where alert_id = a.id and to_status in ('confirmed', 'rejected')) h on h.changed_at is not null
  group by 1;
grant select on stats_weekly_hectares, stats_accuracy, stats_rivers, stats_response_time to authenticated;

-- ---------------------------------------------------------------------------
-- Public map (M11): confirmed sites only, delayed, no scout or tip data.
-- security definer so signed-out visitors can call it without table access.
-- ---------------------------------------------------------------------------
create or replace function public_sites_geojson()
returns json language sql stable security definer set search_path = public as $$
  select json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
    'type', 'Feature',
    'geometry', st_asgeojson(st_simplifypreservetopology(geom, 0.0001), 5)::json,
    'properties', json_build_object('area_ha', area_ha, 'region', region, 'district', district,
      'first_seen', date_trunc('month', first_seen)::date,        -- month only
      'in_forest_reserve', in_forest_reserve, 'forest_reserve_name', forest_reserve_name,
      'river_name', case when river_distance_m <= 500 then river_name end)
  )), '[]'::json))
  from alerts
  where status in ('confirmed', 'field_verified', 'closed')
    and first_seen <= current_date - (select (value #>> '{}')::int from settings where key = 'public_delay_days')
$$;
revoke execute on function public_sites_geojson() from public;
grant execute on function public_sites_geojson() to anon, authenticated;

-- ---------------------------------------------------------------------------
-- Storage buckets (all private; the web app serves short-lived signed URLs)
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public) values
  ('chips', 'chips', false), ('field-photos', 'field-photos', false),
  ('tips', 'tips', false), ('evidence', 'evidence', false)
on conflict (id) do nothing;

create policy chips_read on storage.objects for select
  using (bucket_id = 'chips' and app_role() is not null);
create policy field_photos_read on storage.objects for select
  using (bucket_id = 'field-photos' and (is_staff() or (storage.foldername(name))[1] = auth.uid()::text));
create policy field_photos_insert on storage.objects for insert
  with check (bucket_id = 'field-photos' and (storage.foldername(name))[1] = auth.uid()::text);
create policy tips_media_read on storage.objects for select
  using (bucket_id = 'tips' and is_staff());

-- ---------------------------------------------------------------------------
-- Default connector rows (all off until an agency agreement is in place)
-- ---------------------------------------------------------------------------
insert into connectors (id, agency, type, status, terms) values
  ('minerals_commission_cadastre',  'Minerals Commission',        'cadastre',        'off', 'Not yet agreed'),
  ('minerals_commission_equipment', 'Minerals Commission',        'equipment_gps',   'off', 'Not yet agreed'),
  ('forestry_commission_mining',    'Forestry Commission',        'detections',      'off', 'Not yet agreed'),
  ('cersgis_nrt',                   'CERSGIS',                    'detections',      'off', 'Not yet agreed'),
  ('naimos',                        'NAIMOS',                     'enforcement',     'off', 'Not yet agreed'),
  ('wrc_water_quality',             'Water Resources Commission', 'water_quality',   'off', 'Not yet agreed')
on conflict (id) do nothing;

insert into settings (key, value, description) values
  ('score_weights', '{"base":20,"per_extra_source":15,"max_source_bonus":30,"ai":25,"size_small":5,"size_medium":10,"size_large":15,"persistence":10,"sensitive_zone":20,"river_500m":8,"outside_concession":10,"in_concession":-30,"low_confidence":-10}',
   'Points per scoring factor (see pipeline/galamsey/scoring.py)'),
  ('tip_rate_limit_per_hour', '10', 'Messages per sender per hour before the tip line stops accepting'),
  ('planet_enabled', 'false', 'Paid Planet module (M12). Admin must approve spend first.')
on conflict (key) do nothing;

-- ---------------------------------------------------------------------------
-- Labelling tool (M8)
-- ---------------------------------------------------------------------------
create or replace function add_label(p_geojson text, p_label text, p_image_date date)
returns bigint language sql security invoker set search_path = public as $$
  insert into labels (geom, label, image_date, created_by)
  values (st_setsrid(st_geomfromgeojson(p_geojson), 4326), p_label, p_image_date, auth.uid())
  returning id
$$;
create or replace function labels_geojson()
returns json language sql stable security invoker set search_path = public as $$
  select json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
    'type', 'Feature', 'id', id, 'geometry', st_asgeojson(geom, 6)::json,
    'properties', json_build_object('id', id, 'label', label, 'image_date', image_date, 'from_review', alert_id is not null)
  )), '[]'::json)) from labels
$$;
grant execute on function add_label(text, text, date) to authenticated;
grant execute on function labels_geojson() to authenticated;

-- Analysts need the staff list (names, roles, regions) to assign alerts to scouts.
create policy profiles_staff_read on profiles for select using (is_staff());

-- Draft a high-resolution tasking request (M12). Creates a row only; nothing is ordered
-- until an admin approves it and someone submits it to the provider.
create or replace function draft_tasking(p_alert bigint, p_priority text, p_user uuid)
returns bigint language sql security invoker set search_path = public as $$
  insert into tasking_requests (alert_id, aoi, priority, requested_by)
  select id, st_envelope(st_buffer(geom::geography, 500)::geometry), p_priority, auth.uid()
  from alerts where id = p_alert and level = 'red' and p_user = auth.uid()
  returning id
$$;
grant execute on function draft_tasking(bigint, text, uuid) to authenticated;

-- Field reports from the mobile field mode (M6). client_id makes offline retries safe.
alter table field_reports add column client_id uuid unique;
create or replace function add_field_report(p_client_id uuid, p_alert bigint, p_photos text[], p_notes text,
                                            p_lon float8, p_lat float8, p_visited_at timestamptz)
returns bigint language sql security invoker set search_path = public as $$
  insert into field_reports (client_id, alert_id, user_id, photos, notes, geom, visited_at)
  values (p_client_id, p_alert, auth.uid(), coalesce(p_photos, '{}'), left(p_notes, 4000),
          case when p_lon is null then null else st_setsrid(st_makepoint(p_lon, p_lat), 4326) end, p_visited_at)
  on conflict (client_id) do update set photos = excluded.photos   -- retry of the same report
  returning id
$$;
grant execute on function add_field_report(uuid, bigint, text[], text, float8, float8, timestamptz) to authenticated;

-- Evidence pack context (M9): area growth over time and what is nearby.
create or replace function evidence_context(p_alert bigint)
returns json language sql stable security invoker set search_path = public as $$
  with a as (select * from alerts where id = p_alert),
  det as (select d.* from detections d join alert_sources s on s.detection_id = d.id where s.alert_id = p_alert),
  growth as (
    select dd.image_date,
           round((st_area(st_union(x.geom)::geography) / 10000)::numeric, 2) as cumulative_ha
    from (select distinct image_date from det where image_date is not null) dd
    join det x on x.image_date <= dd.image_date
    group by dd.image_date order by dd.image_date),
  near as (
    select r.type, r.name, round(st_distance(r.geom::geography, a.geom::geography)) as distance_m
    from reference_layers r, a
    where r.type in ('forest_reserve', 'river', 'concession')
      and st_dwithin(r.geom::geography, a.geom::geography, 5000))
  select json_build_object(
    'growth', coalesce((select json_agg(growth) from growth), '[]'::json),
    'nearby', coalesce((select json_agg(n order by n.distance_m) from (select type, name, min(distance_m) as distance_m from near group by type, name) n), '[]'::json),
    'outline', (select st_asgeojson(st_simplifypreservetopology(geom, 0.02), 3)::json from reference_layers where type = 'admin0' limit 1))
$$;
grant execute on function evidence_context(bigint) to authenticated;
