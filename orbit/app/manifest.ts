import type { MetadataRoute } from "next";

/**
 * A web app manifest is what makes ORBIT installable as a Windows window
 * ("Install this site as an app" in Edge/Chrome). That matters for more than
 * taskbar presence: a Chromium app window is exempt from the reserved-key list,
 * so Ctrl+T / Ctrl+W reach the app there. In a normal browser tab they do not.
 *
 * Icons are SVG so there is no binary asset to keep in sync. Chromium accepts
 * SVG icons with `"sizes": "any"`.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ORBIT — Personal AI Command Center",
    short_name: "ORBIT",
    description:
      "One interface. Multiple capabilities. Persistent context. Controlled autonomy.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#07080a",
    theme_color: "#07080a",
    orientation: "landscape-primary",
    categories: ["productivity", "utilities"],
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
    ],
  };
}
