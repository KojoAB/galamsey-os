"use server";

import { revalidatePath } from "next/cache";
import { requireViewer } from "@/lib/auth";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

const ROLES = ["admin", "analyst", "field_scout"];
const E164 = /^\+233\d{9}$/;            // Ghana mobile numbers
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

async function admin() {
  return requireViewer(["admin"]);
}

export async function updateProfile(form: FormData) {
  const me = await admin();
  const userId = String(form.get("user_id"));
  const role = String(form.get("role"));
  if (!ROLES.includes(role)) throw new Error("Invalid role");
  if (userId === me.id && role !== "admin") throw new Error("You cannot remove your own admin role");
  const regions = String(form.get("regions") ?? "").split(",").map((r) => r.trim()).filter(Boolean).slice(0, 16);
  const supabase = await createClient();
  const { error } = await supabase.from("profiles").update({ role, regions, full_name: String(form.get("full_name") ?? "").slice(0, 120) || null }).eq("user_id", userId);
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}

export async function inviteUser(form: FormData) {
  await admin();
  const email = String(form.get("email") ?? "").trim().toLowerCase();
  if (!EMAIL.test(email)) throw new Error("Invalid email");
  const origin = process.env.APP_URL ?? "";
  const { error } = await createAdminClient().auth.admin.inviteUserByEmail(email, { redirectTo: `${origin}/auth/callback` });
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}

export async function updateSetting(form: FormData) {
  const me = await admin();
  const key = String(form.get("key"));
  let value: unknown;
  try { value = JSON.parse(String(form.get("value"))); } catch { throw new Error(`Setting ${key}: value must be valid JSON (e.g. 75, true or {"a":1})`); }
  const supabase = await createClient();
  const { error } = await supabase.from("settings").update({ value, updated_at: new Date().toISOString(), updated_by: me.id }).eq("key", key);
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}

export async function addRecipient(form: FormData) {
  await admin();
  const channel = String(form.get("channel"));
  const address = String(form.get("address") ?? "").trim();
  if (!["whatsapp", "sms", "email"].includes(channel)) throw new Error("Invalid channel");
  if (channel === "email" ? !EMAIL.test(address) : !E164.test(address)) throw new Error("Use +233XXXXXXXXX for phones or a valid email");
  const levels = form.getAll("levels").map(String).filter((l) => ["red", "amber", "watch"].includes(l));
  const supabase = await createClient();
  const { error } = await supabase.from("recipients").insert({ name: String(form.get("name") ?? "").slice(0, 120) || address, channel, address,
    region: String(form.get("region") ?? "") || null, levels: levels.length ? levels : ["red", "amber"], weekly_digest: form.get("weekly") === "on" });
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}

export async function removeRecipient(form: FormData) {
  await admin();
  const supabase = await createClient();
  const { error } = await supabase.from("recipients").delete().eq("id", Number(form.get("id")));
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}

export async function decideTasking(form: FormData) {
  const me = await admin();
  const decision = form.get("decision") === "approve" ? "approved" : "rejected";
  const cost = form.get("cost") ? Number(form.get("cost")) : null;
  const supabase = await createClient();
  const { error } = await supabase.from("tasking_requests")
    .update({ status: decision, approved_by: me.id, decided_at: new Date().toISOString(), estimated_cost_usd: cost })
    .eq("id", Number(form.get("id"))).eq("status", "pending_approval");
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}
