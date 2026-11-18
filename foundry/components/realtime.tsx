"use client";

import * as React from "react";
import { useFoundry } from "@/components/auth-gate";
import { getBrowserSupabase } from "@/lib/supabase/client";

export function useRealtimeRefresh(onChange: () => void, tables: string[] = ["events", "tasks", "workflow_runs", "agents", "approvals"]) {
  const { config, session } = useFoundry();
  const callback = React.useRef(onChange);
  callback.current = onChange;
  const [status, setStatus] = React.useState<"disabled" | "connecting" | "subscribed" | "error">("disabled");

  React.useEffect(() => {
    if (!config.realtimeConfigured || !session) { setStatus("disabled"); return; }
    const client = getBrowserSupabase();
    if (!client) { setStatus("error"); return; }
    setStatus("connecting");
    void client.realtime.setAuth(session.accessToken);
    const channel = client.channel(`foundry:${session.userId}:realtime`);
    for (const table of tables) {
      channel.on("postgres_changes", { event: "*", schema: "public", table, filter: `owner_id=eq.${session.userId}` }, () => callback.current());
    }
    channel.subscribe((next) => setStatus(next === "SUBSCRIBED" ? "subscribed" : next === "CHANNEL_ERROR" || next === "TIMED_OUT" ? "error" : "connecting"));
    return () => { void client.removeChannel(channel); };
  }, [config.realtimeConfigured, session?.userId, session?.accessToken, tables.join(",")]);

  return status;
}
