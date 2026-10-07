#!/usr/bin/env bash
# Installs local Luau tooling for the BRINE repo into the ignored
# .tools/ directory so you can lint/format/analyze the scripts.
#
# Installs:
#   - luaparse (npm)          -> used by tools/check-syntax.js
#   - stylua (ROBLOX formatter)
#   - selene (Lua linter)
#   - luau (compiler/analyzer)
#
# Usage:
#   bash tools/setup-tools.sh
#   RETRIES=5 bash tools/setup-tools.sh   # more resilient to flaky GitHub

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/.tools/bin"
mkdir -p "$BIN"

echo "[1/3] Installing npm luaparse for tools/check-syntax.js ..."
(
	cd "$ROOT/tools"
	npm install --no-fund --no-audit
)

OS=""
ARCH=""
case "$(uname -s)" in
	Linux) OS="linux" ;;
	Darwin) OS="macos" ;;
	MINGW* | MSYS* | CYGWIN*) OS="windows" ;;
	*) echo "Unsupported OS: $(uname -s)"; exit 1 ;;
esac

case "$(uname -m)" in
	x86_64 | amd64) ARCH="x86_64" ;;
	arm64 | aarch64) ARCH="aarch64" ;;
	*) echo "Unsupported arch: $(uname -m)"; exit 1 ;;
esac

RETRIES="${RETRIES:-4}"

# retry_download URL DEST OUTFILE
retry_download() {
	local url="$1" dest="$2" out="$3" attempt=1
	while [ "$attempt" -le "$RETRIES" ]; do
		echo "  attempt $attempt/$RETRIES -> $out"
		if curl -fsSL --retry 2 --retry-delay 2 -o "$dest" "$url" 2>/dev/null; then
			if unzip -oq "$dest" -d "$BIN" 2>/dev/null; then
				rm -f "$dest"
				return 0
			fi
		fi
		echo "  failed, waiting..."
		attempt=$((attempt + 1))
		sleep 2
	done
	echo "  Giving up on $url (set RETRIES higher or use a mirror) [curl: $(command -v curl >/dev/null && echo available || echo missing)]"
	return 1
}

download_zip() {
	local url="$1" name="$2"
	echo "Downloading $name ..."
	retry_download "$url" "$BIN/$name.zip" "$name"
}

echo "[2/3] Downloading stylua and selene ..."

# stylua
STYLUA_ASSET="stylua-${OS}-${ARCH}"
if [ "$OS" = "macos" ]; then
	STYLUA_ASSET="stylua-macos-${ARCH}"
fi
download_zip "https://github.com/JohnnyMorganz/StyLua/releases/download/v2.5.2/${STYLUA_ASSET}.zip" "stylua"

# selene
if [ "$OS" = "linux" ]; then
	SELENE_ASSET="selene-0.31.0-linux.zip"
elif [ "$OS" = "macos" ]; then
	SELENE_ASSET="selene-0.31.0-macos.zip"
else
	SELENE_ASSET="selene-0.31.0-windows.zip"
fi
download_zip "https://github.com/Kampfkarren/selene/releases/download/0.31.0/${SELENE_ASSET}" "selene"

echo "[3/3] Downloading Luau compiler/analyzer ..."
if [ "$OS" = "linux" ]; then
	LUAU_ASSET="luau-ubuntu.zip"
elif [ "$OS" = "macos" ]; then
	LUAU_ASSET="luau-macos.zip"
else
	LUAU_ASSET="luau-windows.zip"
fi
download_zip "https://github.com/luau-lang/luau/releases/download/0.736/${LUAU_ASSET}" "luau"

chmod +x "$BIN"/* 2>/dev/null || true

echo ""
echo "Done. Tools are in $BIN"
echo "Run checks with:"
echo "  node tools/check-syntax.js"
echo "  $BIN/stylua --config-path tools/stylua.toml --check src"
echo "  $BIN/selene --config tools/selene.toml src"
echo "  $BIN/luau-analyze src"
echo ""
echo "TIP: If GitHub release downloads are flaky, pin versions via Rokit:"
echo "  curl -sSf https://raw.githubusercontent.com/rojo-rbx/rokit/main/scripts/install.sh | sh"
echo "  rokit add JohnnyMorganz/StyLua; rokit add Kampfkarren/selene; rokit install"
