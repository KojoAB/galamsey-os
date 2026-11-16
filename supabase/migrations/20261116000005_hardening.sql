-- Galamsey Watch — hardening after Supabase advisor review (16 Nov 2026).
set search_path = public, extensions;

-- 1. Trigger functions are never called directly: nobody needs EXECUTE on them.
revoke execute on function enforce_alert_status() from public, anon, authenticated;
revoke execute on function handle_new_user() from public, anon, authenticated;
-- Role helpers are used inside RLS policies, so signed-in users keep EXECUTE
-- (they only ever reveal the caller's own role/regions). Signed-out visitors don't need them.
revoke execute on function app_role() from public, anon;
revoke execute on function app_regions() from public, anon;
revoke execute on function is_staff() from public, anon;
revoke execute on function is_admin() from public, anon;
grant execute on function app_role(), app_regions(), is_staff(), is_admin() to authenticated;

-- 2. One policy per table/action, with auth.uid() evaluated once per query, not per row.
drop policy alerts_staff_read on alerts;
drop policy alerts_scout_read on alerts;
create policy alerts_read on alerts for select to authenticated
  using ((select is_staff()) or ((select app_role()) = 'field_scout' and region = any((select app_regions())::text[])));

drop policy alerts_staff_update on alerts;
drop policy alerts_scout_verify on alerts;
create policy alerts_update on alerts for update to authenticated
  using ((select is_staff())
         or ((select app_role()) = 'field_scout' and assigned_to = (select auth.uid()) and status = 'confirmed'))
  with check ((select is_staff())
         or (assigned_to = (select auth.uid()) and status in ('confirmed', 'field_verified')));

drop policy profiles_self_read on profiles;
drop policy profiles_staff_read on profiles;
create policy profiles_read on profiles for select to authenticated
  using (user_id = (select auth.uid()) or (select is_staff()));

drop policy reviews_insert on reviews;
create policy reviews_insert on reviews for insert to authenticated
  with check ((select is_staff()) and user_id = (select auth.uid()));

drop policy field_reports_read on field_reports;
drop policy field_reports_insert on field_reports;
create policy field_reports_read on field_reports for select to authenticated
  using ((select is_staff()) or user_id = (select auth.uid()));
create policy field_reports_insert on field_reports for insert to authenticated
  with check (user_id = (select auth.uid()) and (select app_role()) is not null);

drop policy labels_insert on labels;
create policy labels_insert on labels for insert to authenticated
  with check ((select is_staff()) and created_by = (select auth.uid()));

drop policy tasking_insert on tasking_requests;
create policy tasking_insert on tasking_requests for insert to authenticated
  with check ((select is_staff()) and requested_by = (select auth.uid()) and status = 'pending_approval');

drop policy recipients_admin_read on recipients;
drop policy recipients_admin_write on recipients;
create policy recipients_read   on recipients for select to authenticated using ((select is_admin()));
create policy recipients_insert on recipients for insert to authenticated with check ((select is_admin()));
create policy recipients_update on recipients for update to authenticated using ((select is_admin())) with check ((select is_admin()));
create policy recipients_delete on recipients for delete to authenticated using ((select is_admin()));

-- 3. Indexes for the foreign keys the app actually filters or joins on.
create index if not exists alert_sources_detection_idx on alert_sources (detection_id);
create index if not exists alerts_assigned_to_idx     on alerts (assigned_to) where assigned_to is not null;
create index if not exists detections_tile_idx        on detections (tile_id);
create index if not exists enforcement_alert_idx      on enforcement_actions (alert_id);
create index if not exists field_reports_alert_idx    on field_reports (alert_id);
create index if not exists field_reports_user_idx     on field_reports (user_id);
create index if not exists labels_alert_idx           on labels (alert_id);
create index if not exists reviews_alert_idx          on reviews (alert_id);
create index if not exists tasking_alert_idx          on tasking_requests (alert_id);
create index if not exists tips_linked_alert_idx      on tips (linked_alert_id);
create index if not exists tips_phone_recent_idx      on tips (phone_hash, last_message_at desc);

-- Accepted advisor notes (by design):
--  * tip_rate has RLS on and no policies: only the service role (tip webhook) touches it.
--  * public_sites_geojson() is SECURITY DEFINER and callable by anon: it IS the public map,
--    and only returns confirmed, delayed, generalised sites.
