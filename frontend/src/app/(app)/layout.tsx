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
      {/* `min-w-0`: this is a flex item beside the sidebar, and a flex item's
          minimum width defaults to its content's. Without it, one wide table
          row (Knowledge bases' action buttons, Conversations' columns)
          stretched the whole page sideways -- cutting off the top bar --
          instead of scrolling inside the table's own container. */}
      <SidebarInset className="min-w-0">
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
