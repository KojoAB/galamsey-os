// Public Supabase settings. The anon key is safe in the browser: row-level security
// in the database decides what each signed-in user can read.
export const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
export const isConfigured = Boolean(supabaseUrl && supabaseAnonKey);
// Demo mode shows the real tile grid and national outline from /public/demo, with no database.
export const isDemo = process.env.NEXT_PUBLIC_DEMO_MODE === "1" || !isConfigured;
