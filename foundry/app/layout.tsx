import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { getPublicConfig } from "@/lib/public-config";

export const metadata: Metadata = {
  title: "FOUNDRY — Visual Multi-Agent Development Studio",
  description: "Create, orchestrate, monitor and govern general-purpose and Roblox development agents."
};

export const dynamic = "force-dynamic";

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const config = getPublicConfig();
  return (
    <html lang="en" className="dark">
      <body>
        <AuthGate config={config}>
          <AppShell>{children}</AppShell>
        </AuthGate>
      </body>
    </html>
  );
}
