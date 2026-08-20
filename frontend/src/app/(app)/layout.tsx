import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-shell/app-sidebar";
import { Topbar } from "@/components/app-shell/topbar";
import { ImpersonationBanner } from "@/components/app-shell/impersonation-banner";
import { HandoffAlerts } from "@/components/app-shell/handoff-alerts";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider>
      {/* Alerting lives in the shell so a waiting visitor reaches the agent on
          whichever screen they are on — and, via the system notification it
          raises, whether or not the console is the window in front of them. */}
      <HandoffAlerts />
      <AppSidebar />
      <SidebarInset>
        <ImpersonationBanner />
        <Topbar />
        {/* Capped and centred: an operations table stretched across a 2560px
            monitor puts the row's identifier and its actions so far apart that
            you lose track of which row you're acting on. */}
        <main className="flex-1 overflow-y-auto p-6 lg:p-8">
          <div className="mx-auto w-full max-w-[1400px]">{children}</div>
        </main>
      </SidebarInset>
    </SidebarProvider>
  );
}
