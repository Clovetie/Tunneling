---
name: roblox-cross-platform-accessibility
description: Design and test Roblox interfaces and interactions for touch, keyboard, mouse, gamepad, safe areas, readable text, motion sensitivity, and accessible feedback.
---

# Cross-platform controls and accessibility

Use for HUDs, menus, prompts, navigation, and interactions that must work across input devices. Start with `get_studio_state` and `survey_place`; inspect the existing input and UI conventions before changing them.

## Design and implementation
- Make essential actions operable by keyboard, gamepad, and touch; do not rely on hover, a tiny target, precise dragging, color alone, or audio alone. Preserve existing bindings and avoid conflicts with movement and platform controls. Show input-appropriate prompts and update them when the active input changes.
- Use layout constraints, wrapping, and sensible minimum sizes; account for safe areas, text expansion, and on-screen controls. Do not assume desktop dimensions or use TextScaled as a substitute for checking readability.
- Ensure controller focus can reach every actionable element, has an obvious visual indicator, follows a predictable order, and returns to a sensible element after closing a modal. Avoid trapping focus behind overlays.
- Pair color with text, shape, or icon cues. Give feedback for success, failure, and unavailable actions. Offer reduced motion and avoid essential information conveyed only by animation, flashing, or sound.
- Use `audit_ui_layout` for static multi-viewport checks and `capture_viewport` or `screenshot_gui` for visual inspection. These do not prove input accessibility or actual device behavior.

## Verification
Run `luau_check` before script writes. In a connected Studio playtest, check keyboard, touch, and gamepad paths where the relevant device or emulator is available; check small screens, long labels, focus order, modal close and return, and reduced motion. Inspect `get_console_output`. Report untested devices and any inability to verify instead of claiming compliance. Persist changes in the edit DataModel.
