"use client";

import React, { useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  CircleDot,
  KeyRound,
  Link2,
  Plug,
  RefreshCw,
  Shield,
  Unplug,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import { cn, relative } from "@/lib/util";
import { Badge, Button, Modal, StatusPill } from "@/components/ui/kit";
import type { Integration } from "@/lib/types";

const CATEGORY_LABEL: Record<Integration["category"], string> = {
  productivity: "Productivity",
  comms: "Communication",
  storage: "Storage",
  dev: "Development",
  ai: "AI",
  finance: "Finance",
};

export function IntegrationsSection() {
  const { state, dispatch } = useOrbit();
  const actions = useActions();
  const [category, setCategory] = useState<string>("all");
  const [scopeOpen, setScopeOpen] = useState<Integration | null>(null);

  const filtered = useMemo(
    () =>
      state.integrations.filter((i) => (category === "all" ? true : i.category === category)),
    [state.integrations, category],
  );

  const connected = state.integrations.filter((i) => i.status === "connected");
  const errored = state.integrations.filter((i) => i.status === "error");

  function toggle(int: Integration) {
    if (int.status === "connected") {
      dispatch({
        type: "integration.patch",
        id: int.id,
        patch: { status: "available", connectedAt: null, lastSyncAt: null, throughput: "not connected", scopes: [] },
      });
      actions.logActivity({
        kind: "integration",
        title: `${int.name} disconnected`,
        detail: "Tokens revoked locally. No further calls will be made.",
        actor: "You",
        status: "warn",
      });
    } else {
      dispatch({
        type: "integration.patch",
        id: int.id,
        patch: {
          status: "connected",
          connectedAt: new Date().toISOString(),
          lastSyncAt: new Date().toISOString(),
          throughput: "live",
          scopes: int.scopes.length ? int.scopes : ["read"],
        },
      });
      actions.logActivity({
        kind: "integration",
        title: `${int.name} connected`,
        detail: `Scopes granted: ${(int.scopes.length ? int.scopes : ["read"]).join(", ")}`,
        actor: "You",
        status: "ok",
      });
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="border-b border-line px-6 py-4 md:px-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="mb-1 flex items-center gap-1.5">
              <Plug className="h-3.5 w-3.5 text-accent" />
              <span className="text-[10.5px] font-medium tracking-[0.14em] text-faint uppercase">
                Integrations
              </span>
            </div>
            <h1 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">
              Connected surface
            </h1>
            <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-muted">
              Connections are the difference between an answer and a guess. Each one declares the
              scopes it holds, and every call made through it lands in the audit log.
            </p>
          </div>
          <div className="flex gap-2">
            <div className="rounded-[8px] border border-line bg-surface px-3 py-2">
              <div className="num text-[16px] leading-none font-semibold text-signal">
                {connected.length}
              </div>
              <div className="mt-1 text-[9.5px] tracking-wide text-faint uppercase">live</div>
            </div>
            <div className="rounded-[8px] border border-line bg-surface px-3 py-2">
              <div className="num text-[16px] leading-none font-semibold text-danger">
                {errored.length}
              </div>
              <div className="mt-1 text-[9.5px] tracking-wide text-faint uppercase">errors</div>
            </div>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-1">
          {["all", ...Object.keys(CATEGORY_LABEL)].map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={cn(
                "rounded-full px-2.5 py-0.5 text-[10.5px] transition",
                category === c ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
              )}
            >
              {c === "all" ? "All" : CATEGORY_LABEL[c as Integration["category"]]}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-2.5 px-6 py-4 md:px-8 lg:grid-cols-2 xl:grid-cols-3">
        {filtered.map((int) => (
          <div
            key={int.id}
            className={cn(
              "panel flex flex-col p-3.5 transition-colors",
              int.status === "error" && "border-danger/30",
            )}
          >
            <div className="flex items-start gap-2.5">
              <div
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[7px] border text-[13px] font-semibold"
                style={{
                  background: `${int.accent}14`,
                  borderColor: `${int.accent}33`,
                  color: int.accent,
                }}
              >
                {int.glyph}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-[12.5px] font-medium text-ink">{int.name}</span>
                  <StatusPill status={int.status} />
                </div>
                <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-faint">
                  {int.description}
                </p>
              </div>
            </div>

            <div className="mt-2.5 flex flex-wrap gap-1">
              {int.scopes.length ? (
                int.scopes.map((s) => (
                  <span
                    key={s}
                    className="flex items-center gap-1 rounded border border-line bg-abyss px-1 py-[1px] font-mono text-[9px] text-faint"
                  >
                    <KeyRound className="h-2 w-2" />
                    {s}
                  </span>
                ))
              ) : (
                <span className="text-[9.5px] text-faint">No scopes granted</span>
              )}
            </div>

            <div className="mt-auto flex items-center justify-between pt-2.5">
              <div className="text-[9.5px] text-faint">
                {int.lastSyncAt ? (
                  <span className="flex items-center gap-1">
                    <RefreshCw className="h-2 w-2" /> synced {relative(int.lastSyncAt)}
                  </span>
                ) : (
                  <span className="flex items-center gap-1">
                    <CircleDot className="h-2 w-2" /> {int.throughput}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setScopeOpen(int)}
                  className="rounded p-1 text-faint transition hover:bg-raised hover:text-ink"
                  aria-label="Permissions"
                >
                  <Shield className="h-3 w-3" />
                </button>
                {int.status === "connected" ? (
                  <Button size="sm" variant="ghost" onClick={() => toggle(int)}>
                    <Unplug className="h-3 w-3" /> Disconnect
                  </Button>
                ) : (
                  <Button size="sm" variant="secondary" onClick={() => toggle(int)}>
                    <Link2 className="h-3 w-3" />
                    {int.status === "error" ? "Reconnect" : "Connect"}
                  </Button>
                )}
              </div>
            </div>

            {int.status === "error" && (
              <div className="mt-2 flex items-start gap-1.5 rounded-[6px] border border-danger/25 bg-danger/[0.05] px-2 py-1.5">
                <AlertTriangle className="mt-[1px] h-2.5 w-2.5 shrink-0 text-danger" />
                <span className="text-[10px] leading-relaxed text-danger">
                  {int.throughput}. Calls that depend on it will fail explicitly rather than
                  returning stale data.
                </span>
              </div>
            )}
          </div>
        ))}
      </div>

      <Modal
        open={Boolean(scopeOpen)}
        onClose={() => setScopeOpen(null)}
        title={scopeOpen ? `${scopeOpen.name} permissions` : ""}
        description="ORBIT requests the narrowest scope that still lets the tool work."
        width="max-w-lg"
      >
        {scopeOpen && (
          <div className="space-y-2">
            {(scopeOpen.scopes.length ? scopeOpen.scopes : ["read"]).map((s) => (
              <div
                key={s}
                className="flex items-center justify-between rounded-[7px] border border-line bg-abyss/60 px-3 py-2"
              >
                <div>
                  <div className="font-mono text-[11.5px] text-ink">{s}</div>
                  <div className="text-[10px] text-faint">
                    {s.includes("write") || s.includes("create")
                      ? "Write access. Every call is gated and audited."
                      : "Read-only. Cannot modify anything."}
                  </div>
                </div>
                <Check className="h-3 w-3 text-signal" />
              </div>
            ))}
            <div className="rounded-[7px] border border-line bg-abyss/40 px-3 py-2 text-[10.5px] leading-relaxed text-faint">
              Disconnecting revokes the token locally. It does not change permissions on the
              provider side — revoke those in the provider's own settings.
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
