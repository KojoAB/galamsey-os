-- Galamsey Watch — core schema (Milestone 1)
-- All geometries are stored in WGS84 (EPSG:4326). Metric work (areas, buffers,
-- distances) is done in UTM zone 30N (EPSG:32630), which covers Ghana well.

create extension if not exists postgis with schema extensions;   -- Supabase keeps extensions out of public
create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
create type user_role as enum ('admin', 'analyst', 'field_scout');
create type tile_priority as enum ('high', 'normal');
create type alert_level as enum ('red', 'amber', 'watch');
create type alert_status as enum ('new', 'reviewing', 'confirmed', 'rejected', 'field_verified', 'closed');
create type review_decision as enum ('confirm', 'reject', 'needs_more_info');
create type connector_status as enum ('connected', 'stale', 'off');
create type reference_layer_type as enum (
  'admin0', 'admin1', 'admin2',
  'forest_reserve', 'protected_area',
  'river', 'river_buffer_100m', 'river_buffer_500m', 'water_body',
  'concession',
  'priority_zone'
);

-- ---------------------------------------------------------------------------
-- Users and roles (extends Supabase auth.users)
-- ---------------------------------------------------------------------------
create table profiles (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  full_name   text,
  role        user_role not null default 'field_scout',
  regions     text[] not null default '{}',   -- ADM1 names the user covers; empty = none
  phone_e164  text,                            -- staff phone for alerts (not tipsters)
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------
create table reference_layers (
  id            bigint generated always as identity primary key,
  type          reference_layer_type not null,
  name          text,
  geom          geometry(Geometry, 4326) not null,
  attributes    jsonb not null default '{}',
  source        text not null,                 -- e.g. 'geoboundaries:gbOpen', 'minerals_commission:cadastre'
  retrieved_at  timestamptz not null,
  licence       text not null,
  verified      boolean not null default false, -- true only when from the official agency source
  batch_id      uuid not null                   -- one load run; lets us swap layers atomically
);
create index reference_layers_geom_idx on reference_layers using gist (geom);
create index reference_layers_type_idx on reference_layers (type);

-- ---------------------------------------------------------------------------
-- Processing grid
-- ---------------------------------------------------------------------------
create table tiles (
  id                 text primary key,          -- 'E0450_N0620' = SW corner in km, UTM 30N
  geom               geometry(Polygon, 4326) not null,
  priority           tile_priority not null default 'normal',
  region             text,
  last_processed_at  timestamptz
);
create index tiles_geom_idx on tiles using gist (geom);
create index tiles_priority_idx on tiles (priority);

-- ---------------------------------------------------------------------------
-- Detection and alerts
-- ---------------------------------------------------------------------------
create table detections (
  id           bigint generated always as identity primary key,
  source       text not null,                   -- 'gfw_glad_l', 'gfw_radd', 's2_change', 's1_change', 'ai_classifier', ...
  geom         geometry(MultiPolygon, 4326) not null,
  detected_at  timestamptz not null default now(),
  image_date   date,
  confidence   numeric(4,3) check (confidence between 0 and 1),
  metrics      jsonb not null default '{}',
  tile_id      text references tiles(id),
  external_id  text,                            -- provider id, used for idempotent re-runs
  unique (source, external_id)
);
create index detections_geom_idx on detections using gist (geom);
create index detections_image_date_idx on detections (image_date);

create table alerts (
  id                 bigint generated always as identity primary key,
  geom               geometry(MultiPolygon, 4326) not null,
  centroid           geometry(Point, 4326) generated always as (st_pointonsurface(geom)) stored,
  area_ha            numeric(10,2) not null,
  score              smallint not null check (score between 0 and 100),
  score_breakdown    jsonb not null default '{}',
  level              alert_level not null,
  status             alert_status not null default 'new',
  region             text,
  district           text,
  in_forest_reserve  boolean not null default false,
  forest_reserve_name text,
  river_distance_m   integer,
  river_name         text,
  in_concession      boolean not null default false,
  first_seen         date not null,
  last_seen          date not null,
  assigned_to        uuid references auth.users(id),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index alerts_geom_idx on alerts using gist (geom);
create index alerts_region_status_idx on alerts (region, status);
create index alerts_level_idx on alerts (level, first_seen desc);

create table alert_sources (
  alert_id      bigint not null references alerts(id) on delete cascade,
  detection_id  bigint not null references detections(id) on delete cascade,
  primary key (alert_id, detection_id)
);

-- Audit trail of every status change (written by trigger, never by clients)
create table alert_status_history (
  id          bigint generated always as identity primary key,
  alert_id    bigint not null references alerts(id) on delete cascade,
  from_status alert_status,
  to_status   alert_status not null,
  changed_by  uuid references auth.users(id),
  changed_at  timestamptz not null default now(),
  note        text
);
create index alert_status_history_alert_idx on alert_status_history (alert_id, changed_at);

create table reviews (
  id          bigint generated always as identity primary key,
  alert_id    bigint not null references alerts(id) on delete cascade,
  user_id     uuid not null references auth.users(id),
  decision    review_decision not null,
  notes       text,
  created_at  timestamptz not null default now()
);

create table field_reports (
  id          bigint generated always as identity primary key,
  alert_id    bigint references alerts(id) on delete set null,
  user_id     uuid not null references auth.users(id),
  photos      text[] not null default '{}',    -- Supabase Storage object paths
  notes       text,
  geom        geometry(Point, 4326),
  visited_at  timestamptz not null,
  created_at  timestamptz not null default now()
);
create index field_reports_geom_idx on field_reports using gist (geom);

-- Public tip line. Only a salted hash of the phone number is stored.
create table tips (
  id               bigint generated always as identity primary key,
  reference_code   text not null unique,
  channel          text not null default 'whatsapp',
  phone_hash       text not null,
  message          text,
  media            text[] not null default '{}',
  geom             geometry(Point, 4326),
  received_at      timestamptz not null default now(),
  linked_alert_id  bigint references alerts(id) on delete set null
);
create index tips_geom_idx on tips using gist (geom);

create table notifications (
  id          bigint generated always as identity primary key,
  alert_id    bigint references alerts(id) on delete cascade,
  channel     text not null check (channel in ('whatsapp', 'sms', 'email')),
  recipient   text not null,
  sent_at     timestamptz,
  status      text not null default 'queued',  -- queued | sent | delivered | failed
  attempts    smallint not null default 0,
  last_error  text,
  unique (alert_id, channel, recipient)         -- never send the same alert twice
);

create table labels (
  id          bigint generated always as identity primary key,
  geom        geometry(Geometry, 4326) not null,
  label       text not null check (label in ('mine', 'not_mine')),
  image_date  date,
  alert_id    bigint references alerts(id) on delete set null,
  created_by  uuid references auth.users(id),
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Government connectors
-- ---------------------------------------------------------------------------
create table connectors (
  id               text primary key,          -- 'minerals_commission_cadastre', 'cersgis_nrt', ...
  agency           text not null,
  type             text not null,
  status           connector_status not null default 'off',
  last_success_at  timestamptz,
  last_error       text,
  terms            text,                        -- data-sharing terms summary
  public_allowed   boolean not null default false
);

create table equipment_positions (
  id                bigint generated always as identity primary key,
  connector_id      text not null references connectors(id),
  device_id         text not null,
  geom              geometry(Point, 4326) not null,
  recorded_at       timestamptz not null,
  in_concession     boolean,
  in_reserve        boolean,
  river_distance_m  integer,
  unique (connector_id, device_id, recorded_at)
);
create index equipment_positions_geom_idx on equipment_positions using gist (geom);

create table enforcement_actions (
  id           bigint generated always as identity primary key,
  alert_id     bigint not null references alerts(id) on delete cascade,
  agency       text not null,
  action       text not null,                   -- raided | closed | reclaimed | ...
  action_date  date not null,
  notes        text,
  created_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Operations
-- ---------------------------------------------------------------------------
create table pipeline_runs (
  id               bigint generated always as identity primary key,
  job              text not null,               -- 'reference_refresh', 'daily_detection', ...
  started_at       timestamptz not null default now(),
  finished_at      timestamptz,
  status           text not null default 'running',  -- running | success | partial | failed
  tiles_processed  integer not null default 0,
  scenes_used      integer not null default 0,
  alerts_created   integer not null default 0,
  last_tile_id     text,                         -- resume point
  errors           jsonb not null default '[]',
  details          jsonb not null default '{}'
);

-- Admin-editable thresholds and limits
create table settings (
  key         text primary key,
  value       jsonb not null,
  description text,
  updated_at  timestamptz not null default now(),
  updated_by  uuid references auth.users(id)
);

insert into settings (key, value, description) values
  ('min_alert_area_ha',        '0.1',  'Smallest cluster kept as a detection'),
  ('red_min_score',            '75',   'Red = score at or above this AND in forest reserve or river buffer'),
  ('amber_min_score',          '50',   'Amber = score at or above this, outside a concession'),
  ('watch_min_score',          '30',   'Watch = weekly digest only'),
  ('max_alerts_per_region_day','15',   'Alert overload cap, ranked by score'),
  ('fusion_window_days',       '7',    'Overlapping detections within this many days merge into one alert'),
  ('s2_max_cloud_pct',         '60',   'Skip a Sentinel-2 tile above this cloud percentage'),
  ('public_delay_days',        '30',   'Public map shows confirmed sites older than this');
