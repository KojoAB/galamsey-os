import AppShell, { DemoNotice, Page } from "@/components/AppShell";
import { LabelMapLoader } from "@/components/MapLoader";
import { requireViewer } from "@/lib/auth";
import { isDemo } from "@/lib/supabase/env";

export default async function LabelsPage() {
  if (isDemo) return <AppShell viewer={null} demo><Page title="Training labels"><DemoNotice /></Page></AppShell>;
  const viewer = await requireViewer(["admin", "analyst"]);
  return <AppShell viewer={viewer} fullBleed><LabelMapLoader /></AppShell>;
}
