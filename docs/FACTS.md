# FACTS — generated, do not edit

Regenerate with `python3 -B tools/emit_facts.py`. CI and `make docs-check` run
`python3 -B tools/emit_facts.py --check`, which fails when this file is stale, when
the release carries more than one version, or when README.md, CONNECT.md,
AGENTS.md, roblox-bridge/README.md or roblox-bridge/docs/OPS.md states a number
that disagrees with the table below.

This file exists because hand-typed counts drift (P4-D2): the docs claimed 90 tools
against 100 in `TOOL_SPECS`, 11 resources against 12 in `RESOURCES`, a v4.1.0
plugin against a 6.1.0 one, and 20 prefabs against 36.

| Fact | Value | Source |
|---|---|---|
| `bridgeVersion` | 6.1.0 | `server.py` VERSION |
| `pluginVersion` | 6.1.0 | `ArenaBridge.lua` PLUGIN_VERSION |
| `versionStamps` | ArenaBridge.lua 6.1.0, auth_core.py 6.1.0, bus_core.py 6.1.0, confirm_gate.py 6.1.0, mcp_core.py 6.1.0, mock_studio.py 6.1.0, server.py 6.1.0, telemetry.py 6.1.0, tunnel_manager.py 6.1.0 | every version literal in the release |
| `tools` | 100 | `len(mcp_core.TOOL_SPECS)` |
| `resources` | 12 | `len(mcp_core.RESOURCES)` |
| `resourceTemplates` | 3 | `len(mcp_core.RESOURCE_TEMPLATES)` |
| `resourceUris` | studio://state, studio://peers, studio://console, studio://selection, studio://survey, studio://bus, studio://ops, studio://audit, studio://jobs, studio://studios, studio://handoff, mcp://server-card.json | `mcp_core.RESOURCES` |
| `templateUris` | studio://scripts/{path}, studio://instance/{path}, skill://{name} | `mcp_core.RESOURCE_TEMPLATES` |
| `prompts` | 7 | `len(mcp_core.PROMPTS)` |
| `promptNames` | survey_and_architecture, safe_module_edit, automated_playtest_debug, sync_repo_package, multi_agent_handoff, bus_standup, task_triage | `mcp_core.PROMPTS` |
| `skills` | 33 | `search_skills_helper()` total |
| `modules` | 35 | `roblox-kit/modules/*.lua` |
| `systemCategories` | core, liveops, mechanics, monetization, ui | `roblox-kit/systems/*` directories |
| `systems` | 24 | system directories under those categories |
| `uiTemplates` | 6 | `roblox-kit/templates/screens/*.lua` |
| `prefabs` | 36 | `roblox-kit/prefabs/*.json` |
| `models` | 20 | `roblox-kit/models/*.rbxmx` |
| `assets` | 4,320 | `roblox-kit/assets/registry.json` entries |
| `assetFilesOnDisk` | 4,677 | files under `roblox-kit/assets/` (includes docs and contact sheets) |
| `jobs` | 3 | `jobs/*.py` |
| `tests` | 170 | `def test_` methods in `test_bridge_mcp.py` |

## Which prose is a *current* claim

Dated history is not checked, because a count in a release note belongs to that
release. CONNECT.md keeps descending dated notes and only its newest one is scanned;
`roblox-bridge/docs/OPS.md` §7 ("MCP surface changes") records what each v5.x
release changed and is likewise left alone. Everything else in those five documents
is treated as a claim about today.
