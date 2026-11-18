"use client";

import React, { useState } from "react";
import {
  Bot,
  Braces,
  Check,
  Database,
  Gauge,
  KeyRound,
  Layout,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  User,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import { agents, tools } from "@/lib/seed";
import { cn } from "@/lib/util";
import { Badge, Button, Field, Input, Modal, Select, Toggle } from "@/components/ui/kit";

const TABS = [
  { id: "profile", label: "Profile", icon: User },
  { id: "autonomy", label: "Autonomy", icon: ShieldCheck },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "tools", label: "Tools", icon: Braces },
  { id: "appearance", label: "Appearance", icon: Layout },
  { id: "data", label: "Data", icon: Database },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function SettingsSection() {
  const { state, dispatch } = useOrbit();
  const actions = useActions();
  const [tab, setTab] = useState<TabId>("autonomy");
  const [resetOpen, setResetOpen] = useState(false);
  const prefs = state.preferences;

  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-line p-2 md:w-[190px] md:flex-col md:border-r md:border-b-0 md:overflow-visible">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "flex shrink-0 items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-[12px] transition",
                tab === t.id
                  ? "bg-raised text-ink"
                  : "text-muted hover:bg-raised/50 hover:text-ink",
              )}
            >
              <Icon className={cn("h-3.5 w-3.5", tab === t.id ? "text-accent" : "text-faint")} />
              {t.label}
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl px-6 py-5">
          {tab === "profile" && (
            <section className="space-y-4">
              <header>
                <h2 className="text-[16px] font-semibold text-ink">Profile</h2>
                <p className="mt-0.5 text-[12px] text-muted">
                  Used for greetings, time formatting and the briefing window.
                </p>
              </header>
              <div className="panel p-4">
                <div className="mb-4 flex items-center gap-3">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/15 text-[15px] font-semibold text-accent ring-1 ring-accent/25">
                    {state.identity.avatarInitials}
                  </div>
                  <div>
                    <div className="text-[13.5px] font-medium text-ink">{state.identity.name}</div>
                    <div className="text-[11px] text-faint">{state.identity.role}</div>
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Display name">
                    <Input
                      value={prefs.displayName}
                      onChange={(e) => actions.setPrefs({ displayName: e.target.value })}
                    />
                  </Field>
                  <Field label="Timezone" hint="IANA">
                    <Input
                      value={prefs.timezone}
                      onChange={(e) => actions.setPrefs({ timezone: e.target.value })}
                    />
                  </Field>
                </div>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <Field label="Week starts">
                    <Select
                      value={prefs.weekStart}
                      onChange={(e) =>
                        actions.setPrefs({ weekStart: e.target.value as "monday" | "sunday" })
                      }
                    >
                      <option value="monday">Monday</option>
                      <option value="sunday">Sunday</option>
                    </Select>
                  </Field>
                  <Field label="Agent verbosity">
                    <Select
                      value={prefs.agentVerbosity}
                      onChange={(e) =>
                        actions.setPrefs({
                          agentVerbosity: e.target.value as typeof prefs.agentVerbosity,
                        })
                      }
                    >
                      <option value="concise">Concise</option>
                      <option value="balanced">Balanced</option>
                      <option value="detailed">Detailed</option>
                    </Select>
                  </Field>
                </div>
              </div>
            </section>
          )}

          {tab === "autonomy" && (
            <section className="space-y-4">
              <header>
                <h2 className="text-[16px] font-semibold text-ink">Controlled autonomy</h2>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
                  These two switches define how much ORBIT may do without asking. Read actions are
                  cheap to reverse; writes are not.
                </p>
              </header>

              <div className="panel divide-y divide-line px-4">
                <Toggle
                  checked={prefs.autoApproveReads}
                  onChange={(v) => actions.setPrefs({ autoApproveReads: v })}
                  label="Auto-approve read tools"
                  description="Calendar reads, knowledge search and file analysis run without a prompt. They cannot change state."
                />
                <Toggle
                  checked={prefs.confirmAllWrites}
                  onChange={(v) => actions.setPrefs({ confirmAllWrites: v })}
                  label="Confirm all writes"
                  description="Every create or update parks at the approval gate. Leave this on unless you are supervising a batch."
                />
              </div>

              <div className="rounded-[8px] border border-warn/25 bg-warn/[0.05] p-3">
                <div className="flex items-center gap-1.5">
                  <ShieldCheck className="h-3.5 w-3.5 text-warn" />
                  <span className="text-[11.5px] font-medium text-ink">Current posture</span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-muted">
                  {prefs.confirmAllWrites
                    ? "Writes are gated. ORBIT will propose an action, show you the exact tool and arguments, and wait. Nothing is applied until you approve."
                    : "Writes are live. ORBIT can create tasks, events and notes directly. Every action still writes an audit entry you can inspect in Activity."}
                </p>
              </div>

              <div className="panel overflow-hidden">
                <header className="border-b border-line px-3.5 py-2 text-[11px] font-medium text-ink">
                  Permission model
                </header>
                <div className="divide-y divide-line/60">
                  {[
                    { level: "read", note: "No state change. Auto-approved when enabled.", tone: "success" },
                    { level: "write", note: "Creates or mutates local entities. Gated.", tone: "warn" },
                    { level: "execute", note: "Runs workflows or shells. Gated + audited.", tone: "warn" },
                    { level: "external", note: "Leaves the machine. Gated, scoped, audited.", tone: "danger" },
                  ].map((p) => (
                    <div key={p.level} className="flex items-center gap-3 px-3.5 py-2">
                      <Badge tone={p.tone as "success" | "warn" | "danger"}>{p.level}</Badge>
                      <span className="text-[11px] text-muted">{p.note}</span>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          )}

          {tab === "agents" && (
            <section className="space-y-3">
              <header>
                <h2 className="text-[16px] font-semibold text-ink">Agent roster</h2>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
                  Each agent has distinct tools and hard boundaries. The Supervisor decides when
                  delegation is worth the overhead — it does not delegate for the appearance of
                  sophistication.
                </p>
              </header>
              {agents.map((a) => (
                <div key={a.id} className="panel p-3.5">
                  <div className="flex items-start gap-2.5">
                    <div
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[6px] border text-[11px] font-semibold"
                      style={{
                        background: `${a.accent}14`,
                        borderColor: `${a.accent}33`,
                        color: a.accent,
                      }}
                    >
                      {a.name[0]}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[12.5px] font-medium text-ink">{a.name}</span>
                        <span className="text-[10px] text-faint">{a.role}</span>
                        <Badge tone="neutral" className="ml-auto">
                          {a.tools.length} tools
                        </Badge>
                      </div>
                      <p className="mt-1 text-[11px] leading-relaxed text-muted">{a.description}</p>
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {a.tools.map((t) => (
                          <span
                            key={t}
                            className="rounded border border-line bg-abyss px-1 py-[1px] font-mono text-[9px] text-faint"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                      <div className="mt-1.5 space-y-0.5">
                        {a.boundaries.map((b) => (
                          <div key={b} className="flex items-start gap-1.5">
                            <ShieldCheck className="mt-[2px] h-2.5 w-2.5 shrink-0 text-faint" />
                            <span className="text-[10px] text-faint">{b}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </section>
          )}

          {tab === "tools" && (
            <section className="space-y-3">
              <header>
                <h2 className="text-[16px] font-semibold text-ink">Tool registry</h2>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
                  {tools.length} registered tools. Every one carries an id, a schema, a permission
                  level, a validator and an audit path. A tool that cannot verify its own result
                  cannot report success.
                </p>
              </header>
              <div className="panel overflow-hidden">
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="border-b border-line bg-raised/50">
                      {["Tool", "Permission", "Approval", "Required args"].map((h) => (
                        <th
                          key={h}
                          className="px-3 py-1.5 text-left text-[9.5px] font-medium tracking-wide text-faint uppercase"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tools.map((t) => (
                      <tr key={t.id} className="border-b border-line/50 last:border-0">
                        <td className="px-3 py-1.5">
                          <div className="font-mono text-[11px] text-ink">{t.id}</div>
                          <div className="text-[9.5px] text-faint">{t.description}</div>
                        </td>
                        <td className="px-3 py-1.5">
                          <Badge
                            tone={
                              t.permission === "read"
                                ? "success"
                                : t.permission === "external"
                                  ? "danger"
                                  : "warn"
                            }
                          >
                            {t.permission}
                          </Badge>
                        </td>
                        <td className="px-3 py-1.5">
                          {t.requiresApproval ? (
                            <span className="text-[10px] text-warn">gated</span>
                          ) : (
                            <span className="text-[10px] text-signal">auto</span>
                          )}
                        </td>
                        <td className="px-3 py-1.5">
                          <div className="flex flex-wrap gap-1">
                            {t.inputSchema
                              .filter((f) => f.required)
                              .map((f) => (
                                <span
                                  key={f.field}
                                  className="rounded border border-line px-1 font-mono text-[9px] text-faint"
                                >
                                  {f.field}
                                </span>
                              ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {tab === "appearance" && (
            <section className="space-y-4">
              <header>
                <h2 className="text-[16px] font-semibold text-ink">Appearance</h2>
                <p className="mt-0.5 text-[12px] text-muted">
                  Dark-first, restrained. Accent colour is the only decorative variable.
                </p>
              </header>
              <div className="panel p-4">
                <Field label="Accent">
                  <div className="flex gap-2">
                    {[
                      { id: "indigo", c: "#7c9cff" },
                      { id: "mint", c: "#4ade9b" },
                      { id: "violet", c: "#a78bfa" },
                      { id: "amber", c: "#f5b556" },
                    ].map((a) => (
                      <button
                        key={a.id}
                        onClick={() =>
                          actions.setPrefs({ accent: a.id as typeof prefs.accent })
                        }
                        className={cn(
                          "flex h-8 flex-1 items-center justify-center gap-1.5 rounded-[7px] border text-[11px] capitalize transition",
                          prefs.accent === a.id
                            ? "border-line-bright bg-raised text-ink"
                            : "border-line text-faint hover:text-muted",
                        )}
                      >
                        <span
                          className="h-2.5 w-2.5 rounded-full"
                          style={{ background: a.c }}
                        />
                        {a.id}
                      </button>
                    ))}
                  </div>
                </Field>
                <div className="mt-2 divide-y divide-line">
                  <Toggle
                    checked={prefs.reducedMotion}
                    onChange={(v) => actions.setPrefs({ reducedMotion: v })}
                    label="Reduce motion"
                    description="Disables entrance animation and the live pulse. Your OS setting is also respected."
                  />
                  <Toggle
                    checked={!prefs.sidebarCollapsed}
                    onChange={(v) => actions.setPrefs({ sidebarCollapsed: !v })}
                    label="Expanded sidebar"
                    description="Collapsing gives about 180px back to the workspace."
                  />
                  <Toggle
                    checked={prefs.rightPanelOpen}
                    onChange={(v) => actions.setPrefs({ rightPanelOpen: v })}
                    label="Intelligence panel"
                    description="Contextual panel on the right. Drag its edge to resize."
                  />
                </div>
                <div className="mt-3">
                  <Field label="Panel width" hint={`${prefs.rightPanelWidth}px`}>
                    <input
                      type="range"
                      min={280}
                      max={520}
                      value={prefs.rightPanelWidth}
                      onChange={(e) =>
                        actions.setPrefs({ rightPanelWidth: Number(e.target.value) })
                      }
                      className="w-full accent-[var(--color-accent)]"
                    />
                  </Field>
                </div>
              </div>
            </section>
          )}

          {tab === "data" && (
            <section className="space-y-4">
              <header>
                <h2 className="text-[16px] font-semibold text-ink">Data & state</h2>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
                  ORBIT persists to this browser only. No account, no server, no sync. Export
                  before resetting if you want the record.
                </p>
              </header>
              <div className="panel divide-y divide-line p-4">
                <div className="flex items-center justify-between py-2">
                  <div>
                    <div className="text-[12.5px] text-ink">Current state</div>
                    <div className="text-[10.5px] text-faint">
                      {state.projects.length} projects · {state.tasks.length} tasks ·{" "}
                      {state.notes.length} notes · {state.files.length} files ·{" "}
                      {state.conversations.length} conversations
                    </div>
                  </div>
                  <Badge tone="success" dot>
                    v{state.seedVersion}
                  </Badge>
                </div>
                <div className="flex items-center justify-between py-2.5">
                  <div>
                    <div className="text-[12.5px] text-ink">Export everything</div>
                    <div className="text-[10.5px] text-faint">
                      Full JSON snapshot of projects, tasks, notes, automations and conversations.
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      const blob = new Blob([JSON.stringify(state, null, 2)], {
                        type: "application/json",
                      });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `orbit-export-${new Date().toISOString().slice(0, 10)}.json`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                  >
                    <Gauge className="h-3 w-3" /> Export
                  </Button>
                </div>
                <div className="flex items-center justify-between py-2.5">
                  <div>
                    <div className="text-[12.5px] text-ink">Reset to seed</div>
                    <div className="text-[10.5px] text-faint">
                      Replaces local state with the grounded starting dataset. Cannot be undone.
                    </div>
                  </div>
                  <Button size="sm" variant="danger" onClick={() => setResetOpen(true)}>
                    <RotateCcw className="h-3 w-3" /> Reset
                  </Button>
                </div>
              </div>
              <div className="rounded-[8px] border border-line bg-abyss/50 p-3">
                <div className="flex items-center gap-1.5">
                  <KeyRound className="h-3 w-3 text-faint" />
                  <span className="text-[10px] font-medium tracking-wide text-faint uppercase">
                    Secrets
                  </span>
                </div>
                <p className="mt-1 text-[10.5px] leading-relaxed text-faint">
                  No API keys are stored in this build. Provider credentials stay in the host
                  environment; the UI never reads them and state never contains them.
                </p>
              </div>
            </section>
          )}
        </div>
      </div>

      <Modal
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        title="Reset ORBIT state?"
        description="This replaces every local project, task, note and conversation with the seed dataset."
        width="max-w-md"
      >
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setResetOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              actions.resetAll();
              setResetOpen(false);
            }}
          >
            <Trash2 className="h-3 w-3" /> Reset everything
          </Button>
        </div>
      </Modal>
    </div>
  );
}
