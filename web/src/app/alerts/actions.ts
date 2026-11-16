"use server";

import { revalidatePath } from "next/cache";
import { requireViewer } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

const ids = (form: FormData) => form.getAll("ids").map(Number).filter((n) => Number.isInteger(n) && n > 0);

// Every action re-checks the role here AND relies on row-level security in the database.
export async function assignAlerts(form: FormData) {
  await requireViewer(["admin", "analyst"]);
  const list = ids(form);
  const to = String(form.get("assignee") ?? "");
  if (!list.length) return;
  const supabase = await createClient();
  const { error } = await supabase.from("alerts").update({ assigned_to: to || null }).in("id", list);
  if (error) throw new Error(error.message);
  revalidatePath("/alerts");
}

export async function reviewAlert(form: FormData) {
  await requireViewer(["admin", "analyst"]);
  const id = Number(form.get("alert_id"));
  const decision = String(form.get("decision"));
  if (!["confirm", "reject", "needs_more_info"].includes(decision)) throw new Error("Invalid decision");
  const notes = String(form.get("notes") ?? "").slice(0, 4000) || null;
  const supabase = await createClient();
  const { error } = await supabase.rpc("review_alert", { p_alert: id, p_decision: decision, p_notes: notes });
  if (error) throw new Error(error.message);
  revalidatePath(`/alerts/${id}`);
}

export async function setAlertStatus(form: FormData) {
  await requireViewer();
  const id = Number(form.get("alert_id"));
  const status = String(form.get("status"));
  if (!["field_verified", "closed", "reviewing"].includes(status)) throw new Error("Invalid status");
  const supabase = await createClient();
  // The database trigger rejects any transition that isn't allowed.
  const { error } = await supabase.from("alerts").update({ status }).eq("id", id);
  if (error) throw new Error(error.message);
  revalidatePath(`/alerts/${id}`);
}

export async function addEnforcement(form: FormData) {
  await requireViewer(["admin", "analyst"]);
  const id = Number(form.get("alert_id"));
  const supabase = await createClient();
  const { error } = await supabase.from("enforcement_actions").insert({
    alert_id: id, agency: String(form.get("agency") || "NAIMOS").slice(0, 100),
    action: String(form.get("action") || "").slice(0, 50), action_date: String(form.get("action_date")),
    notes: String(form.get("notes") ?? "").slice(0, 2000) || null,
  });
  if (error) throw new Error(error.message);
  revalidatePath(`/alerts/${id}`);
}

export async function requestTasking(form: FormData) {
  const v = await requireViewer(["admin", "analyst"]);
  const id = Number(form.get("alert_id"));
  const priority = form.get("priority") === "urgent" ? "urgent" : "standard";
  const supabase = await createClient();
  // AOI = the alert's bounding box plus 500 m, computed in the database.
  const { data: a, error: e1 } = await supabase.from("alerts").select("id, level").eq("id", id).single();
  if (e1 || !a) throw new Error("Alert not found");
  if (a.level !== "red") throw new Error("High-resolution tasking is only offered for Red alerts");
  const { error } = await supabase.rpc("draft_tasking", { p_alert: id, p_priority: priority, p_user: v.id });
  if (error) throw new Error(error.message);
  revalidatePath(`/alerts/${id}`);
}
