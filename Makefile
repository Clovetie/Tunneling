# Local commands, identical to the strings CI runs (P4-D6.2). `make ci` is the whole
# gate. Nothing here needs a Studio peer or a network service; `lint` needs ruff, a
# lint-time dependency (tools/check_lint.sh prints the install command and exits 2
# rather than passing silently when it is missing).

PYTHON ?= python3
BRIDGE := roblox-bridge

.DEFAULT_GOAL := help

.PHONY: help test test-fast test-unit lint verify docs-check facts luau kit harness secrets ci

help:	## print these targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

test:	## full regression suite, ~215 s (what CI runs)
	cd $(BRIDGE) && $(PYTHON) -B test_bridge_mcp.py

test-fast:	## every test, minus the two nested self-test suite runs, ~71 s
	cd $(BRIDGE) && $(PYTHON) -B test_bridge_mcp.py --fast

test-unit:	## offline classes only: no port is bound, 63 tests in ~2 s
	cd $(BRIDGE) && $(PYTHON) -B test_bridge_mcp.py --unit

lint:	## ruff, plus the blind-except ratchet
	bash tools/check_lint.sh

verify:	## kit asset licence manifest
	$(PYTHON) -B tools/verify_licences.py

docs-check:	## docs/FACTS.md and every doc claim agree with the tree
	$(PYTHON) -B tools/emit_facts.py --check

facts:	## regenerate docs/FACTS.md
	$(PYTHON) -B tools/emit_facts.py

luau:	## undefined-global scan over the bridge and the kit's Luau
	bash $(BRIDGE)/tools/check_globals.sh $(BRIDGE)
	bash $(BRIDGE)/tools/check_globals.sh roblox-kit/modules
	bash $(BRIDGE)/tools/check_globals.sh roblox-kit/systems

kit:	## committed asset manifest matches the tree (writes nothing)
	$(PYTHON) $(BRIDGE)/image_pipeline.py --check

harness:	## dashboard XSS/CSP contract (needs node)
	node $(BRIDGE)/tools/dashboard_harness.js

secrets:	## nothing credential-shaped is tracked
	bash tools/check_secrets.sh

ci: luau lint verify kit docs-check harness secrets test	## every gate, in CI order
