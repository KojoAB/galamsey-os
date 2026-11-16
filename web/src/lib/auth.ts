import "server-only";
import { redirect } from "next/navigation";
import { isDemo } from "./supabase/env";
import { createClient } from "./supabase/server";

export type Role = "admin" | "analyst" | "field_scout";
export type Viewer = { id: string; email?: string; role: Role; regions: string[] };

// Who is signed in, with their role. Pages call this; the database's RLS is still
// what enforces access — this only decides what to render.
export async function getViewer(): Promise<Viewer | null> {
  if (isDemo) return null;
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  if (!data?.claims) return null;
  const { data: p } = await supabase.from("profiles").select("role, regions").eq("user_id", data.claims.sub).single();
  return { id: data.claims.sub, email: data.claims.email as string | undefined, role: (p?.role ?? "field_scout") as Role, regions: p?.regions ?? [] };
}

export async function requireViewer(roles?: Role[]): Promise<Viewer> {
  const v = await getViewer();
  if (!v) redirect("/login");
  if (roles && !roles.includes(v.role)) redirect("/");
  return v;
}

export const isStaff = (v: Viewer | null) => v?.role === "admin" || v?.role === "analyst";
