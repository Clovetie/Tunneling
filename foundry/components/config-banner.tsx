"use client";

import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { useFoundry } from "@/components/auth-gate";
import { InlineNotice } from "@/components/ui";
import { cn } from "@/lib/utils";

export function ConfigBanner({ compact = false }: { compact?: boolean }) {
  const { config } = useFoundry();
  if (config.mode === "ready") return null;
  return (
    <InlineNotice tone={config.mode === "unconfigured" ? "amber" : "cyan"} title={config.mode === "unconfigured" ? "Local preview — persistence is not configured" : "Partial configuration"}>
      <div className={cn("grid gap-1", compact ? "" : "sm:grid-cols-2")}>
        {config.warnings.map((warning) => <div key={warning} className="flex items-start gap-1.5"><Info className="mt-0.5 h-3 w-3 shrink-0" /><span>{warning}</span></div>)}
      </div>
    </InlineNotice>
  );
}

export function ReadinessStrip() {
  const { config } = useFoundry();
  const items = [
    { label: "Auth", ok: config.authConfigured },
    { label: "Database", ok: config.databaseConfigured },
    { label: "Realtime", ok: config.realtimeConfigured },
    { label: "Secrets", ok: config.encryptionConfigured },
    { label: "Worker", ok: config.workerConfigured },
    { label: "Models", ok: config.providers.length > 0 }
  ];
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {items.map((item) => (
        <span key={item.label} className={cn("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-2xs", item.ok ? "border-signal-green/20 bg-signal-green/8 text-signal-green" : "border-signal-amber/20 bg-signal-amber/8 text-signal-amber")}>
          {item.ok ? <CheckCircle2 className="h-2.5 w-2.5" /> : <AlertTriangle className="h-2.5 w-2.5" />}{item.label}
        </span>
      ))}
    </div>
  );
}
