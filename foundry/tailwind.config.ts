import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#090b0e",
        graphite: {
          950: "#0a0c10",
          900: "#101318",
          850: "#141920",
          800: "#1a2028",
          750: "#202730",
          700: "#2a323d",
          600: "#3b4654"
        },
        line: "#252d38",
        ink: {
          DEFAULT: "#e7ebf0",
          muted: "#8f9aa8",
          faint: "#5d6876"
        },
        signal: {
          cyan: "#73d6cf",
          blue: "#7aa7ff",
          violet: "#a88cff",
          amber: "#e7b86a",
          rose: "#f08491",
          green: "#75d69a"
        }
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "Liberation Mono", "monospace"]
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }]
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,0.03) inset, 0 14px 40px rgba(0,0,0,0.28)",
        glow: "0 0 0 1px rgba(115,214,207,0.22), 0 0 28px rgba(115,214,207,0.08)"
      },
      keyframes: {
        fadeIn: { from: { opacity: "0", transform: "translateY(3px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        softPulse: { "0%,100%": { opacity: "0.65" }, "50%": { opacity: "1" } },
        flow: { from: { strokeDashoffset: "18" }, to: { strokeDashoffset: "0" } }
      },
      animation: {
        "fade-in": "fadeIn 220ms ease-out both",
        "soft-pulse": "softPulse 2.2s ease-in-out infinite",
        "flow-dash": "flow 1.2s linear infinite"
      }
    }
  },
  plugins: []
};

export default config;
