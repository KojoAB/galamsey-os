"use client";

import { useActionState } from "react";
import { sendMagicLink, type LoginState } from "./actions";

export default function LoginPage() {
  const [state, action, pending] = useActionState<LoginState, FormData>(sendMagicLink, null);
  return (
    <main className="flex min-h-dvh items-center justify-center bg-ink-950 p-6">
      <form action={action} className="w-full max-w-sm space-y-5 rounded-2xl border border-ink-700 bg-ink-900 p-7">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-400">Galamsey Watch</p>
          <h1 className="mt-2 text-2xl font-semibold text-white">Sign in</h1>
          <p className="mt-1 text-sm text-ink-300">We will email you a one-time sign-in link.</p>
        </div>
        <label className="block">
          <span className="text-sm font-medium text-ink-200">Work email</span>
          <input
            name="email" type="email" required autoComplete="email" inputMode="email"
            className="mt-1.5 w-full rounded-lg border border-ink-600 bg-ink-950 px-3.5 py-3 text-base text-white outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-400/30"
          />
        </label>
        <button
          disabled={pending}
          className="w-full rounded-lg bg-brand-500 px-4 py-3 font-semibold text-white hover:bg-brand-400 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-300 disabled:opacity-60"
        >
          {pending ? "Sending…" : "Email me a link"}
        </button>
        {state && (
          <p role="status" className={`text-sm ${state.ok ? "text-brand-300" : "text-alert-red"}`}>{state.message}</p>
        )}
        <p className="text-xs text-ink-400">Accounts are created by an administrator.</p>
      </form>
    </main>
  );
}
