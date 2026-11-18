"use client";

import React, { useEffect, useState } from "react";
import { Modal } from "@/components/ui/kit";
import { GO_ROWS } from "@/lib/keys";
import { SECTION_LABEL } from "@/lib/tabs";

const GROUPS: { title: string; rows: [string, string][] }[] = [
  {
    title: "Global",
    rows: [
      ["⌘ K  /  Ctrl K", "Command palette"],
      ["/", "Command palette"],
      ["⇧ ⌘ F", "Search all entities"],
      ["\\", "Collapse / expand sidebar"],
      ["]", "Show / hide intelligence panel"],
      ["?", "This help"],
      ["Esc", "Dismiss overlay"],
    ],
  },
  {
    title: "Tabs & panes",
    rows: [
      ["Alt T", "New tab (Home)"],
      ["Alt ⇧ T", "Reopen closed tab"],
      ["Alt W", "Close active tab"],
      ["Ctrl ⇥", "Next tab"],
      ["Ctrl ⇧ ⇥", "Previous tab"],
      ["double-click", "Rename workspace"],
    ],
  },
  {
    title: "Assistant",
    rows: [
      ["⌘ ↵", "Send message"],
      ["⇧ ↵", "New line"],
      ["⌘ 1–6", "Switch mode (Chat → Agent)"],
      ["⌘ .", "Stop generation"],
    ],
  },
  {
    title: "Navigation",
    rows: GO_ROWS.map(([k, id]) => [k, SECTION_LABEL[id]] as [string, string]),
  },
];

export function KeyboardHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Keyboard shortcuts"
      description="ORBIT is designed to be driven without touching the sidebar."
      width="max-w-xl"
    >
      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {GROUPS.map((g) => (
          <div key={g.title}>
            <div className="mb-2 text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              {g.title}
            </div>
            <div className="space-y-1.5">
              {g.rows.map(([k, label]) => (
                <div key={k} className="flex items-center justify-between gap-2">
                  <span className="text-[11.5px] text-muted">{label}</span>
                  <kbd className="shrink-0 rounded border border-line bg-abyss px-1.5 py-0.5 font-mono text-[10px] text-faint">
                    {k}
                  </kbd>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
