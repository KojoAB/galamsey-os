"use server";

import { headers } from "next/headers";
import { createClient } from "@/lib/supabase/server";

export type LoginState = { ok: boolean; message: string } | null;

// Email magic-link sign-in. New accounts start as field_scout with no regions;
// an admin assigns roles. Self sign-up can also be switched off in Supabase Auth settings.
export async function sendMagicLink(_prev: LoginState, form: FormData): Promise<LoginState> {
  const email = String(form.get("email") ?? "").trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return { ok: false, message: "Enter a valid email address." };
  }
  const origin = (await headers()).get("origin") ?? "";
  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: `${origin}/auth/callback`, shouldCreateUser: false },
  });
  // Same message whether or not the account exists, so the form can't be used to probe for staff emails.
  if (error && error.status !== 400 && error.status !== 422) {
    return { ok: false, message: "Could not send the link right now. Try again in a minute." };
  }
  return { ok: true, message: "If this email has an account, a sign-in link is on its way." };
}
