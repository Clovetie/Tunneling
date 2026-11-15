# Anti-Slop & Visual Clarity Audit Checklist

## 1. Banned AI Punctuation & Connector Tokens (Flagged by `de_slop_audit`)

| Slop Category | Banned Tokens / Patterns | Clean Game-Ready Replacement |
|---|---|---|
| **Em-Dashes** | `—` (`\u2014`), `–` (`\u2013`), ` -- ` | Use `·` for HUD badges, `:` for key/value, or `.` for sentences |
| **Academic Connectors** | `Moreover`, `Furthermore`, `Additionally`, `Consequently`, `Nevertheless`, `Nonetheless`, `Henceforth` | Delete entirely; start directly with the verb or noun |
| **LLM Cliché Nouns/Verbs** | `delve`, `tapestry`, `testament`, `symbiotic`, `kaleidoscope`, `beacon of`, `embark on a journey`, `unleash your` | Concrete nouns & verbs (`open`, `strike`, `unlock`, `craft`, `survive`) |
| **Rhetorical Framing** | `"It's not just X, it's Y"`, `"Whether you're a X or a Y"`, `"Let's dive in"` | State the mechanic or reward directly |

## 2. Visual Hierarchy & HUD Density Targets

| Metric | Target | Why |
|---|---|---|
| **Max words on a `TextButton`** | `1–3 words` | Players scan buttons in `< 150ms`; sentences slow reaction time |
| **Max characters on a `TextLabel` (HUD)** | `<= 65 chars` | Prevents `TextScaled` from shrinking font below readable `11px` on mobile |
| **Icon-to-Button Ratio** | `>= 1 icon per primary action` | Icons communicate action category before text is read |
| **3D Mesh Ratio (`MeshPart` / Props)** | Prioritize `spawn_kit_mesh` / `.rbxmx` | Eliminates "AI greybox block-slop" in 3D environments |
