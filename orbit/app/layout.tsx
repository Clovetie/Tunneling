import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ORBIT — Personal AI Command Center",
  description:
    "One interface. Multiple capabilities. Persistent context. Controlled autonomy.",
  applicationName: "ORBIT",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "ORBIT", statusBarStyle: "black-translucent" },
  // ORBIT is a local-first personal tool; nothing here is meant for indexing.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#07080a",
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
