import { agents, tools } from "./seed";
import {
  atLocalHour,
  dayKey,
  fmtTime,
  minutesUntil,
  relative,
  sortByPriority,
} from "./util";
import type { FeedItem, OrbitState, Priority } from "./types";

export interface BriefingLine {
  kind: "schedule" | "deadline" | "overdue" | "blocked" | "priority" | "routine";
  text: string;
  detail?: string;
  refId?: string;
}

export interface Briefing {
  generatedAt: string;
  greetingLine: string;
  headline: string;
  lines: BriefingLine[];
  counts: {
    eventsToday: number;
    openTasks: number;
    overdue: number;
    blocked: number;
    focusMinutes: number;
    activeProjects: number;
  };
  suggestion: string;
  grounded: boolean;
}

const dayEvents = (state: OrbitState, offset = 0) => {
  const key = dayKey(atLocalHour(offset, 12));
  return state.events
    .filter((e) => dayKey(e.start) === key)
    .sort((a, b) => a.start.localeCompare(b.start));
};

/**
 * Briefing is computed only from state. If a source has no data, we say so
 * instead of inventing it (see `grounded` and the explicit "no signal" lines).
 */
export function buildBriefing(state: OrbitState): Briefing {
  const today = dayEvents(state, 0);
  const open = state.tasks.filter((t) => t.status !== "done");
  const overdue = open.filter((t) => t.due && new Date(t.due).getTime() < Date.now());
  const blocked = open.filter((t) => t.status === "blocked");
  const focus = today.filter((e) => e.kind === "focus" || e.kind === "deadline");
  const focusMinutes = focus.reduce(
    (acc, e) => acc + Math.max(0, (new Date(e.end).getTime() - new Date(e.start).getTime()) / 60000),
    0,
  );
  const activeProjects = state.projects.filter((p) => p.status === "active");
  const next = today.find((e) => minutesUntil(e.end) > 0);
  const calendarConnected = state.integrations.find((i) => i.id === "int-cal")?.status === "connected";

  const lines: BriefingLine[] = [];

  if (!calendarConnected) {
    lines.push({
      kind: "schedule",
      text: "Calendar is not connected, so I cannot confirm today's schedule.",
      detail: "Reconnect Google Calendar in Integrations to restore schedule grounding.",
    });
  } else if (today.length === 0) {
    lines.push({
      kind: "schedule",
      text: "No events on the calendar today.",
      detail: "Nothing scheduled; the day is fully open.",
    });
  } else {
    if (next) {
      const started = minutesUntil(next.start) <= 0;
      lines.push({
        kind: "schedule",
        text: started
          ? `In progress: ${next.title}, ends ${fmtTime(next.end)}`
          : `Next up: ${next.title} at ${fmtTime(next.start)}`,
        detail: `${started ? `${minutesUntil(next.end)}m left` : relative(next.start)} · ${today.filter((e) => minutesUntil(e.end) > 0).length} blocks left today`,
        refId: next.id,
      });
    }
    lines.push({
      kind: "schedule",
      text: `${today.length} events today, ${focus.length} of them focus or deadline blocks`,
      detail: `${Math.round(focusMinutes)} minutes of protected time`,
    });
  }

  const deadlines = today.filter((e) => e.kind === "deadline");
  for (const d of deadlines.slice(0, 2)) {
    const passed = minutesUntil(d.end) < 0;
    lines.push({
      kind: passed ? "overdue" : "deadline",
      text: passed
        ? `${d.title} passed at ${fmtTime(d.end)}`
        : `${d.title} at ${fmtTime(d.start)}`,
      detail: passed ? `${relative(d.end)} — already closed` : relative(d.start),
      refId: d.id,
    });
  }

  if (overdue.length) {
    lines.push({
      kind: "overdue",
      text: `${overdue.length} task${overdue.length === 1 ? "" : "s"} past due`,
      detail: overdue
        .slice(0, 3)
        .map((t) => t.title)
        .join(" · "),
    });
  }

  if (blocked.length) {
    lines.push({
      kind: "blocked",
      text: `${blocked.length} blocked`,
      detail: blocked.map((t) => `${t.title} (${t.notes || "no reason recorded"})`).join(" · "),
    });
  }

  const ranked = sortByPriority(
    open.filter((t) => t.status === "doing" || (t.due && minutesUntil(t.due) < 24 * 60)),
  ).slice(0, 3);
  for (const t of ranked) {
    const project = state.projects.find((p) => p.id === t.projectId);
    lines.push({
      kind: "priority",
      text: t.title,
      detail: `${project ? project.name : "No project"} · ${t.due ? relative(t.due) : "no due date"}`,
      refId: t.id,
    });
  }

  const headline = (() => {
    if (blocked.length >= 2) return `${blocked.length} things are blocked. Clear one before starting anything new.`;
    if (overdue.length) return `${overdue.length} overdue items and ${focus.length} focus blocks. Tight but workable.`;
    if (ranked[0]) return `${ranked[0].title} is the highest-leverage item today.`;
    return "Nothing is on fire. Good day to pull work forward.";
  })();

  const suggestion = (() => {
    const first = ranked[0];
    if (first && first.estimateMin && first.estimateMin <= 60)
      return `Start with "${first.title}" — it fits in ${first.estimateMin} minutes.`;
    // Only call a block "uninterrupted" if it is actually long enough to work in.
    const longBlock = focus.find(
      (e) => (new Date(e.end).getTime() - new Date(e.start).getTime()) / 60000 >= 30,
    );
    if (longBlock)
      return `Protect the ${fmtTime(longBlock.start)}–${fmtTime(longBlock.end)} block; it is your longest run of uninterrupted time today.`;
    if (first)
      return `"${first.title}" is the item with the most force behind it; it has no estimate, so give it one before you start.`;
    const shortDeadline = focus[0];
    if (shortDeadline)
      return `${shortDeadline.title} at ${fmtTime(shortDeadline.start)} is a hard window — the only fixed point today.`;
    return "No protected time today. Consider carving a 45-minute block.";
  })();

  return {
    generatedAt: new Date().toISOString(),
    greetingLine: next
      ? minutesUntil(next.start) <= 0
        ? `Now: ${next.title} · ends in ${minutesUntil(next.end)}m`
        : `Next: ${next.title} · ${relative(next.start)}`
      : "No upcoming block",
    headline,
    lines,
    counts: {
      eventsToday: today.length,
      openTasks: open.length,
      overdue: overdue.length,
      blocked: blocked.length,
      focusMinutes: Math.round(focusMinutes),
      activeProjects: activeProjects.length,
    },
    suggestion,
    grounded: calendarConnected,
  };
}

/** Feed items are derived, then merged with recorded events — never invented. */
export function deriveFeed(state: OrbitState): FeedItem[] {
  const items: FeedItem[] = [];
  const nowMs = Date.now();
  const open = state.tasks.filter((t) => t.status !== "done");

  for (const t of open) {
    if (t.status === "blocked") {
      items.push({
        id: `feed-blocked-${t.id}`,
        at: t.createdAt,
        kind: "attention",
        title: `Blocked: ${t.title}`,
        detail: t.notes || "No block reason recorded.",
        actionLabel: "Open task",
        actionTarget: { section: "tasks", id: t.id },
        priority: t.priority,
      });
    } else if (t.due && new Date(t.due).getTime() < nowMs) {
      items.push({
        id: `feed-overdue-${t.id}`,
        at: t.due,
        kind: "task",
        title: `Overdue: ${t.title}`,
        detail: `Was due ${relative(t.due)}.`,
        actionLabel: "Open task",
        actionTarget: { section: "tasks", id: t.id },
        priority: "high",
      });
    }
  }

  for (const p of state.projects.filter((x) => x.status === "active")) {
    const lateMs = p.milestones.filter((m) => !m.done && m.due && new Date(m.due).getTime() < nowMs);
    if (lateMs.length) {
      items.push({
        id: `feed-ms-${p.id}`,
        at: lateMs[0].due!,
        kind: "project",
        title: `${p.name}: ${lateMs.length} milestone${lateMs.length === 1 ? "" : "s"} slipped`,
        detail: lateMs.map((m) => m.title).join(" · "),
        actionLabel: "Open project",
        actionTarget: { section: "projects", id: p.id },
        priority: lateMs.length > 1 ? "critical" : "high",
      });
    }
  }

  for (const i of state.integrations) {
    if (i.status === "error") {
      items.push({
        id: `feed-int-${i.id}`,
        at: i.lastSyncAt ?? new Date().toISOString(),
        kind: "integration",
        title: `${i.name} needs attention`,
        detail: `${i.throughput}. No writes were attempted while disconnected.`,
        actionLabel: "Open integrations",
        actionTarget: { section: "integrations", id: i.id },
        priority: "medium",
      });
    }
  }

  for (const a of state.automations) {
    const failed = a.runs.find((r) => r.status === "failed");
    if (failed && !a.enabled) {
      items.push({
        id: `feed-auto-${a.id}`,
        at: failed.at,
        kind: "automation",
        title: `${a.name} is paused after a failure`,
        detail: failed.log.slice(-1)[0] ?? "Run failed.",
        actionLabel: "Inspect",
        actionTarget: { section: "automations", id: a.id },
        priority: "medium",
      });
    }
  }

  const rank: Record<Priority, number> = { critical: 0, high: 1, medium: 2, low: 3 };
  return items
    .sort((a, b) => rank[a.priority] - rank[b.priority] || b.at.localeCompare(a.at))
    .slice(0, 10);
}

export const toolCatalog = tools;
export const agentCatalog = agents;
