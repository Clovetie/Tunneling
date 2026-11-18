"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, Bell, Bot, BookOpen, Boxes, FolderKanban, Gamepad2, GitBranch, LayoutDashboard,
  Menu, PanelLeft, Plug, Search, Settings, ShieldCheck, UserRound, X
} from "lucide-react";
import { useFoundry } from "@/components/auth-gate";
import { ConfigBanner, ReadinessStrip } from "@/components/config-banner";
import { Badge, Button, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/builder", label: "Agent Builder", icon: GitBranch },
  { href: "/workflows", label: "Workflows", icon: Boxes },
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/activity", label: "Activity", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings }
];

const roblox = { href: "/roblox", label: "Roblox Workspace", icon: Gamepad2 };

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { config, session } = useFoundry();
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const [collapsed, setCollapsed] = React.useState(false);

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-line px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-md border border-signal-cyan/25 bg-signal-cyan/10 font-mono text-xs font-bold text-signal-cyan">F</div>
        {!collapsed ? <div className="min-w-0"><div className="font-mono text-xs font-semibold tracking-[0.2em] text-ink">FOUNDRY</div><div className="truncate text-2xs text-ink-faint">Agent Development Studio</div></div> : null}
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-3">
        {!collapsed ? <div className="px-2 pb-1 font-mono text-2xs uppercase tracking-[0.18em] text-ink-faint">Workspace</div> : null}
        <nav className="space-y-0.5">
          {nav.map((item) => <NavItem key={item.href} {...item} active={pathname.startsWith(item.href)} collapsed={collapsed} />)}
        </nav>
        <div className="my-3 border-t border-line" />
        {!collapsed ? <div className="px-2 pb-1 font-mono text-2xs uppercase tracking-[0.18em] text-ink-faint">Connected environments</div> : null}
        <NavItem {...roblox} active={pathname.startsWith(roblox.href)} collapsed={collapsed} accent />
        {!collapsed ? (
          <div className="mt-4 rounded-lg border border-line bg-graphite-950/60 p-2.5">
            <div className="flex items-center gap-1.5 text-2xs font-medium text-ink-muted"><ShieldCheck className="h-3 w-3 text-signal-cyan" /> Credential boundary</div>
            <p className="mt-1 text-2xs leading-relaxed text-ink-faint">Bridge, provider and tunnel secrets stay server-side. Agents receive capabilities, never credentials.</p>
          </div>
        ) : null}
      </div>
      <div className="shrink-0 border-t border-line p-2">
        <button onClick={() => setCollapsed((v) => !v)} className="flex h-8 w-full items-center justify-center gap-2 rounded-md text-2xs text-ink-faint hover:bg-graphite-850 hover:text-ink">
          <PanelLeft className="h-3.5 w-3.5" />{!collapsed ? "Collapse" : ""}
        </button>
      </div>
    </div>
  );

  return (
    <TooltipProvider delayDuration={250}>
      <div className="flex min-h-screen">
        <aside className={cn("fixed inset-y-0 left-0 z-40 hidden border-r border-line bg-graphite-950/95 backdrop-blur md:block transition-[width] duration-200", collapsed ? "w-14" : "w-60")}>{sidebar}</aside>
        {mobileOpen ? (
          <div className="fixed inset-0 z-50 md:hidden">
            <div className="absolute inset-0 bg-black/70" onClick={() => setMobileOpen(false)} />
            <aside className="absolute inset-y-0 left-0 w-64 border-r border-line bg-graphite-950">{sidebar}</aside>
            <button className="absolute right-4 top-4 rounded-md border border-line bg-graphite-900 p-2 text-ink-muted" onClick={() => setMobileOpen(false)}><X className="h-4 w-4" /></button>
          </div>
        ) : null}
        <div className={cn("flex min-h-screen min-w-0 flex-1 flex-col transition-[padding] duration-200", collapsed ? "md:pl-14" : "md:pl-60")}>
          <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-graphite-950/90 px-3 backdrop-blur-md sm:px-5">
            <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileOpen(true)}><Menu className="h-4 w-4" /></Button>
            <div className="hidden min-w-0 items-center gap-2 text-xs text-ink-faint sm:flex"><span className="font-mono text-signal-cyan/70">~/foundry</span><span className="text-graphite-600">/</span><span className="truncate text-ink-muted">{pathname.split("/").filter(Boolean).join(" / ") || "overview"}</span></div>
            <div className="ml-auto flex items-center gap-2">
              <div className="hidden lg:block"><ReadinessStrip /></div>
              <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon"><Search className="h-3.5 w-3.5" /></Button></TooltipTrigger><TooltipContent>Global search is available in Knowledge and Activity</TooltipContent></Tooltip>
              <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" className="relative"><Bell className="h-3.5 w-3.5" /><span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-signal-amber" /></Button></TooltipTrigger><TooltipContent>Approval and failure notifications appear in Activity</TooltipContent></Tooltip>
              <Badge tone={config.mode === "ready" ? "green" : "amber"}>{config.mode}</Badge>
              <div className="hidden items-center gap-1.5 rounded-md border border-line bg-graphite-900 px-2 py-1 text-2xs text-ink-muted sm:flex"><UserRound className="h-3 w-3" />{session?.email ?? "preview"}</div>
              {session ? <Button variant="secondary" size="sm" onClick={() => void session.signOut()}>Sign out</Button> : null}
            </div>
          </header>
          <main className="min-w-0 flex-1 px-3 py-5 sm:px-5 lg:px-7">
            <div className="mx-auto w-full max-w-[1500px] space-y-4">
              <ConfigBanner />
              {children}
            </div>
          </main>
          <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2 font-mono text-2xs text-ink-faint">
            <span>FOUNDRY 0.1.0 · secondary orchestrator</span>
            <span>Tunneling integration uses runtime MCP discovery</span>
          </footer>
        </div>
      </div>
    </TooltipProvider>
  );
}

function NavItem({ href, label, icon: Icon, active, collapsed, accent }: { href: string; label: string; icon: React.ComponentType<{ className?: string }>; active: boolean; collapsed: boolean; accent?: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link href={href} className={cn(
          "group flex h-8 items-center gap-2.5 rounded-md px-2 text-xs transition-colors",
          active ? "border border-signal-cyan/20 bg-signal-cyan/10 text-signal-cyan" : "border border-transparent text-ink-muted hover:bg-graphite-850 hover:text-ink",
          accent && !active && "text-signal-violet/80 hover:text-signal-violet",
          collapsed && "justify-center px-0"
        )}>
          <Icon className={cn("h-3.5 w-3.5 shrink-0", active ? "text-signal-cyan" : "text-ink-faint group-hover:text-ink-muted")} />
          {!collapsed ? <span className="truncate">{label}</span> : null}
        </Link>
      </TooltipTrigger>
      {collapsed ? <TooltipContent side="right">{label}</TooltipContent> : null}
    </Tooltip>
  );
}
