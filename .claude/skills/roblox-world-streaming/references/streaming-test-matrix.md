# Streaming test matrix

Record place revision, Studio/client version, device, network conditions, player count, streaming configuration, route, expected behavior and observation for each run. Use a non-production test place and isolated test data with permission. Do not alter live settings for a test.

| Scenario | Observe on each client | Observe on server | Failure / recovery |
|---|---|---|---|
| Join / spawn | First usable UI; nearby target availability; loading does not wait forever | Character and authoritative initial state | Missing target, slow arrival, leave during loading |
| Traverse far and return | Reacquisition, prompt/UI state, listener duplication, local visual changes | Shared state remains authoritative | Target unavailable, stream-out and re-entry |
| Distant move / teleport | Transitional feedback and destination readiness | Destination validation, request outcome and cleanup | Delay/failure, invalid destination, departure mid-request |
| Respawn / rejoin | Hints and object bindings recover without stale references | Eligibility and persistent state remain correct | Race with target availability or data load failure |
| Two clients in different regions | Independent availability and correct feedback | Consistent shared state and concurrent requests | Client sees no target; another player changes it |
| Resource-constrained device | Memory, frame pacing and responsiveness on actual hardware | Server/load context and observed errors | Compare repeatable scenarios; no extrapolation from mock counts |

After a settings or code change, repeat the same routes and test conditions and compare results. A client's local reference may hold stale property values after stream-out; a remote can refer to an instance not yet present on that client. Document timing and missing targets rather than interpreting nonappearance as proof of a particular engine state. `Atomic` models do not guarantee atomic delivery of descendants added after initial replication. Check current official documentation before relying on engine-specific details:

- [Instance streaming](https://create.roblox.com/docs/workspace/streaming)
- [Streaming techniques and conversion](https://create.roblox.com/docs/workspace/streaming/techniques)

The matrix does not simulate or force streaming. Offline and mock checks are not evidence that a Studio client, physical device, live network or multi-client session passed.
