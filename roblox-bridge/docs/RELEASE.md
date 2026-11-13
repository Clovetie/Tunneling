# Arena Roblox Bridge — ready-to-use source distribution

This archive contains the bridge, Studio plugin source, bundled skills, local kit assets and licenses. It is a Python source distribution, **not** a signed native installer or a preconfigured Studio plugin. No token, existing MCP configuration, snapshots, or runtime state is included.

## Install and start

1. Extract the entire archive to a writable folder. Keep `roblox-bridge`, `roblox-kit`, and `.claude/skills` together. You need Python 3.10+ and Roblox Studio on a supported Windows or macOS machine. The bundled `luau-compile`/`luau-analyze` binaries are Linux builds; obtain appropriate native tools separately on other platforms if needed. On Unix, use an extractor that preserves executable permissions, or explicitly enable the supplied tools after verifying them.
2. Close or restart Studio when updating the plugin. In a terminal, run:
   ```sh
   cd roblox-bridge
   python setup.py --write-mcp
   ```
   This creates or reuses a local token, installs a token-configured plugin, writes `.mcp.json` in the current directory, and starts the local bridge. It can recover a token from an existing plugin; `--rotate` changes the token. Inspect `setup.py --help` before changing an existing setup. **Do not share the token or generated plugin file.**
3. Open Studio with HTTP requests enabled, then open an authorized test place. Confirm the plugin is enabled. Run `python arena_studio.py doctor` from `roblox-bridge`; `connected` reports registry presence, not proof of a working Studio job. Configure your MCP client using the generated `.mcp.json` or the examples in `roblox-bridge/README.md`.
4. For an Inspector-only bridge, use `python setup.py --read-only --write-mcp`. Read-only mode is not a sandbox. Do not edit a live place or production data without explicit permission. Never publish a tunnel without understanding token access and the separate trust boundaries described in the README.

## Verify

Run `python -B roblox-bridge/test_bridge_mcp.py` from the extracted root for offline and mock regression tests. `python -B tools/verify_licences.py` checks bundled asset provenance locally. Actual Studio/plugin, physical device, live networking and multi-client behavior require separate authorized testing; the archive is not a guarantee of those results. See `roblox-bridge/README.md`, `roblox-bridge/docs/OVERHAUL.md`, and `THIRD_PARTY_LICENSES.md` for features, limitations and licenses.

`MANIFEST.sha256` lists SHA-256 hashes of archive contents except the manifest itself. Verify the downloaded ZIP checksum separately against the `.sha256` file supplied with the archive. Keep the archive and checksum from a trusted source; a checksum does not establish publisher identity.
