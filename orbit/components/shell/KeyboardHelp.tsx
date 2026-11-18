"use client";

import React, { useEffect, useState } from "react";
import { Modal } from "@/components/ui/kit";
import { helpGroups } from "@/lib/keys";
import { usePlatform } from "@/lib/platform";
import { SECTION_LABEL } from "@/lib/tabs";

export function KeyboardHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { platform, appWindow } = usePlatform();
  const groups = helpGroups(platform, { appWindow });
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Keyboard shortcuts"
      description="ORBIT is designed to be driven without touching the sidebar."
      width="max-w-xl"
    >
      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {groups.map((g) => (
          <div key={g.title}>
            <div className="mb-2 text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              {g.title}
            </div>
            <div className="space-y-1.5">
              {g.rows.map(([k, label]) => (
                <div key={k} className="flex items-center justify-between gap-2">
                  <span className="text-[11.5px] text-muted">
                    {label in SECTION_LABEL ? SECTION_LABEL[label as keyof typeof SECTION_LABEL] : label}
                  </span>
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
