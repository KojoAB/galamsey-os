-- Galamsey Watch — functions the map and pipeline call.

-- GeoJSON FeatureCollection for one reference layer, simplified for the web.
-- security invoker: RLS on reference_layers still applies to the caller.
-- tolerance is in degrees (0.0005 ≈ 55 m) — enough for a national map.
create or replace function layer_geojson(layer reference_layer_type, tolerance float8 default 0.0005)
returns json
language sql stable security invoker set search_path = public as $$
  select json_build_object(
    'type', 'FeatureCollection',
    'features', coalesce(json_agg(json_build_object(
      'type', 'Feature',
      'id', id,
      'geometry', st_asgeojson(st_simplifypreservetopology(geom, tolerance), 6)::json,
      'properties', json_build_object(
        'name', name, 'source', source, 'verified', verified,
        'retrieved_at', retrieved_at, 'licence', licence)
    )), '[]'::json))
  from reference_layers
  where type = layer
$$;

create or replace function tiles_geojson()
returns json
language sql stable security invoker set search_path = public as $$
  select json_build_object(
    'type', 'FeatureCollection',
    'features', coalesce(json_agg(json_build_object(
      'type', 'Feature',
      'id', id,
      'geometry', st_asgeojson(geom, 5)::json,
      'properties', json_build_object('id', id, 'priority', priority, 'region', region,
                                      'last_processed_at', last_processed_at)
    )), '[]'::json))
  from tiles
$$;

-- Layer inventory for the map legend / admin panel: what is loaded, from where, how fresh.
create or replace view reference_layer_summary with (security_invoker = true) as
  select type, source, licence, verified, max(retrieved_at) as retrieved_at, count(*) as features
  from reference_layers
  group by type, source, licence, verified;

-- Atomic layer swap used by the loader: delete the previous batch of a type
-- only after the new batch is fully inserted. Service role only.
create or replace function swap_reference_batch(layer reference_layer_type, new_batch uuid)
returns integer
language plpgsql security definer set search_path = public as $$
declare removed integer;
begin
  delete from reference_layers where type = layer and batch_id <> new_batch;
  get diagnostics removed = row_count;
  return removed;
end $$;
revoke execute on function swap_reference_batch(reference_layer_type, uuid) from public, anon, authenticated;

-- Tag each tile with its region and set priority = high where it touches a priority zone.
create or replace function refresh_tile_attributes()
returns void
language sql security definer set search_path = public as $$
  -- Region = the ADM1 polygon covering most of the tile (tiles on a border touch two).
  update tiles t set region = (
    select r.name from reference_layers r
    where r.type = 'admin1' and st_intersects(r.geom, t.geom)
    order by st_area(st_intersection(r.geom, t.geom)) desc limit 1
  );

  update tiles t set priority = case when exists (
    select 1 from reference_layers z
    where z.type = 'priority_zone' and st_intersects(z.geom, t.geom)
  ) then 'high'::tile_priority else 'normal'::tile_priority end;
$$;
revoke execute on function refresh_tile_attributes() from public, anon, authenticated;

-- Explicit Data API grants (newer Supabase projects do not grant these automatically).
-- Row-level security still decides which rows each user sees.
grant execute on function layer_geojson(reference_layer_type, float8) to authenticated;
grant execute on function tiles_geojson() to authenticated;
grant select on reference_layer_summary to authenticated;
