import AppShell from "@/components/AppShell";
import MapLoader from "@/components/MapLoader";
import { getViewer, isStaff } from "@/lib/auth";
import { isDemo } from "@/lib/supabase/env";
import { redirect } from "next/navigation";

export default async function Home() {
  const viewer = await getViewer();
  if (!isDemo && !viewer) redirect("/login");
  return (
    <AppShell viewer={viewer} demo={isDemo} fullBleed>
      <MapLoader demo={isDemo} staff={isStaff(viewer)} />
    </AppShell>
  );
}
