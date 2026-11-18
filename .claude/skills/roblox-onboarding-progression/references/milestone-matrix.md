# First-session milestone matrix

Fill this from the actual experience; timings and rewards are hypotheses to test, not defaults to impose.

| Stage | Entry condition | Player-visible objective and feedback | Server completion evidence | Retry, skip, and rejoin behavior | Observation |
|---|---|---|---|---|---|
| Orient | New or returning player enters | What is visible, readable and actionable at spawn? | None required for viewing a hint | Can the player dismiss or revisit guidance? What if UI/target is missing? | Device, locale, revision, spawn conditions |
| First action | Target available and player eligible | One specific action and clear result | Validate target, eligibility and authoritative state | Repeated input, invalid request, target unavailable, respawn | Eligible starts and completion; record test conditions |
| First milestone | Valid action completed | Explain the outcome and next choice | Server confirms completion and any reward | Idempotent reward; failed save and rejoin recovery | Completion and failure reasons, not inferred motivation |
| Next objective | Prior milestone confirmed | Voluntary next step with visible navigation | Check prerequisites from server state | Alternate path and resume after leaving | Observe confusion and unintended gating |

Before instrumenting, define event names and denominators, avoid sensitive player data, and agree on the permitted test environment. For each device/input and locale, record completion, blocked states, errors and qualitative observations. Separate offline/mock evidence from real Studio, multi-client, device and player testing. Re-run comparable scenarios after a change; do not infer causation or a durable improvement from a single session.
