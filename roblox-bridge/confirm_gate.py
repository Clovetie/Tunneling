#!/usr/bin/env python3
"""
confirm_gate.py — human-in-the-loop confirmation for dangerous MCP tool calls
(v5.4.0, stdlib-only).

Why this exists
---------------
An agent driving Studio can delete a subtree, rewrite a hundred scripts, or
push a place file. `--read-only` is a big switch (nothing may write); the
confirmation gate is the small one (this call may write, but only after a human
said yes). It is modelled on Roblox's own Studio MCP "confirm dangerous
operation" flow, and on the MCP 2026-07-28 **Multi Round-Trip Requests**
pattern (SEP-2322): the server does *not* hold the connection open waiting for
a human, it answers `resultType: "input_required"` with an elicitation form and
a signed, opaque `requestState`; the client re-issues the same call with
`inputResponses` once a person has answered.

Two ways to answer
------------------
1. **In-band (modern clients)** — the client's retry carries `requestState` +
   `inputResponses`. Verified: HMAC signature, principal binding, expiry,
   request digest (a token cannot be replayed onto a different call), single
   use.
2. **Out-of-band (anything that cannot do MRTR)** — the bridge keeps the
   pending request in a bounded registry, so a human can answer from the
   dashboard, `POST /api/confirmations`, or `ab.ps1 approve <id>`. Legacy-era
   (pre-2026-07-28) MCP clients block on this path, because that era still
   allowed a server to wait for its client.

Modes
-----
===========  =========================================================
`off`         no gating (default; nothing changes for existing users)
`destructive` only tools annotated `destructiveHint` are gated
`write`       every tool that is not annotated read-only is gated
`all`         every tool call is gated
===========  =========================================================

A granted approval may be **remembered** for a bounded window
(`allowance_seconds`), which keeps a long multi-step session from stalling on
every single write while still requiring a single human "yes".
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

GATE_VERSION = "5.4.0"
GATE_MODES = ("off", "destructive", "write", "all")

#: What each mode actually gates. Reported in `status()` so an operator reading
#: the queue can see the policy in force, not just the name of it.
GATE_SCOPES = {
    "off": "nothing",
    "destructive": "tools with destructiveHint, plus the operator-curated list",
    "write": "every tool that is not annotated readOnlyHint",
    "all": "every tool, reads included",
}

#: How long an unanswered request stays answerable.
DEFAULT_TTL_SECONDS = 180
MIN_TTL_SECONDS = 15
MAX_TTL_SECONDS = 900
#: How long an approved "remember this" grant stays valid.
DEFAULT_ALLOWANCE_SECONDS = 600
MAX_ALLOWANCE_SECONDS = 3600
#: Bounded registry so a chatty agent cannot grow memory without limit.
MAX_PENDING = 100
MAX_DECISION_HISTORY = 200

_STATE_PENDING = "pending"
_STATE_APPROVED = "approved"
_STATE_DENIED = "denied"
_STATE_EXPIRED = "expired"


def mode_from_env(env=None):
    """`BRIDGE_REQUIRE_CONFIRM` / `--require-confirm` accept several spellings."""
    raw = (os.environ if env is None else env).get("BRIDGE_REQUIRE_CONFIRM")
    if raw is None:
        return "off"
    value = str(raw).strip().lower()
    if value in ("", "0", "off", "no", "false", "none"):
        return "off"
    if value in ("1", "true", "yes", "on", "destructive"):
        return "destructive"
    if value in ("write", "rw"):
        return "write"
    if value in ("all", "everything"):
        return "all"
    return value if value in GATE_MODES else "off"


def canonical_digest(tool: str, arguments: dict) -> str:
    """Stable digest of the exact call a token was minted for."""
    blob = json.dumps(
        {"tool": tool, "arguments": arguments or {}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text.encode("ascii") + pad.encode("ascii"))


class GateError(RuntimeError):
    """A verification failure with a machine-readable reason."""

    def __init__(self, reason: str, message: str = None):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


class Confirmation:
    """One pending (or resolved) approval for one tool call."""

    __slots__ = ("id", "tool", "arguments", "principal", "nonce", "digest",
                 "created", "expires", "state", "answer", "note", "remember",
                 "event", "decision_source", "danger_keys")

    def __init__(self, tool, arguments, principal, nonce, digest, ttl, danger_keys=None):
        now = time.time()
        self.id = "cfm-" + nonce[:12]
        self.tool = tool
        self.arguments = arguments or {}
        self.principal = principal
        self.nonce = nonce
        self.digest = digest
        self.created = now
        self.expires = now + ttl
        self.state = _STATE_PENDING
        self.answer = None
        self.note = None
        self.remember = False
        self.decision_source = None
        self.danger_keys = list(danger_keys or [])
        self.event = threading.Event()

    # -- serialisation -------------------------------------------------------

    def public(self) -> dict:
        return {
            "requestId": self.id,
            "tool": self.tool,
            "principal": self.principal,
            "state": self.state,
            "createdAt": self._iso(self.created),
            "expiresAt": self._iso(self.expires),
            "secondsRemaining": max(0, round(self.expires - time.time(), 1)),
            "argumentsPreview": self._preview(self.arguments),
            "decisionSource": self.decision_source,
            "note": self.note,
            "remembered": bool(self.remember),
        }

    @staticmethod
    def _iso(ts: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

    @staticmethod
    def _preview(args: dict, limit: int = 180) -> str:
        try:
            text = json.dumps(args, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(args)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def to_input_requests(self, detail: dict = None) -> dict:
        """The `InputRequests` map for an InputRequiredResult (SEP-2322)."""
        keys = sorted(self.danger_keys or [])
        bullets = "\n".join(f"- {k}" for k in keys[:6]) or "- (no argument payload)"
        message = (
            f"Confirm `{self.tool}`? The bridge is running with "
            f"--require-confirm={detail.get('mode', 'destructive') if detail else 'destructive'}.\n"
            f"Target: {json.dumps(self.arguments.get('path') or self.arguments.get('root') or self.arguments.get('id') or '.', ensure_ascii=False)}\n"
            f"Mutating argument keys:\n{bullets}\n"
            f"Args: {self._preview(self.arguments, 400)}"
        )
        schema = {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["approve", "deny"],
                    "description": "approve runs the tool exactly as requested; deny cancels it.",
                },
                "remember": {
                    "type": "boolean",
                    "description": (
                        "When true, subsequent calls to this same tool by this principal "
                        "skip the gate for a bounded window (default 10 minutes)."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "Optional note recorded in the audit chain (shown back to the agent).",
                },
            },
            "required": ["decision"],
        }
        return {
            "confirm": {
                "method": "elicitation/create",
                "params": {
                    "mode": "form",
                    "message": message,
                    "requestedSchema": schema,
                },
            }
        }


class ConfirmGate:
    """Signed-request confirmation gate. Thread-safe, in-memory, bounded."""

    def __init__(
        self,
        mode: str = "off",
        secret: str = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        allowance_seconds: float = DEFAULT_ALLOWANCE_SECONDS,
        hook=None,
        destructive_tools=None,
        gated_tools=None,
    ):
        self.mode = mode if mode in GATE_MODES else mode_from_env()
        self.ttl_seconds = max(MIN_TTL_SECONDS, min(float(ttl_seconds or DEFAULT_TTL_SECONDS), MAX_TTL_SECONDS))
        self.allowance_seconds = max(0.0, min(float(allowance_seconds or 0), MAX_ALLOWANCE_SECONDS))
        self.hook = hook  # callable(kind: str, detail: dict) -> None
        # `destructive` mode gates the operator-curated set plus anything the
        # tool itself advertises with destructiveHint.
        self.destructive_tools = frozenset(destructive_tools or ())
        if gated_tools:
            self.destructive_tools = self.destructive_tools | frozenset(gated_tools)
        key = secret or os.environ.get("BRIDGE_CONFIRM_SECRET") or ""
        if not key:
            # No shared secret: the pending registry is process-local anyway.
            key = "local:" + secrets.token_hex(16)
        self._key = hashlib.sha256(f"arena-confirm-v1|{key}".encode("utf-8")).digest()
        self._lock = threading.RLock()
        self._pending = {}
        self._history = []
        self._consumed = {}          # nonce -> expiry (single-use enforcement)
        self._decisions = {}         # requestId -> answered decision awaiting a retry
        self._allowances = {}        # "tool|principal" -> expiry timestamp
        self.counters = {"requested": 0, "approved": 0, "denied": 0,
                         "expired": 0, "allowed_by_window": 0}

    # -- configuration -------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def status(self) -> dict:
        with self._lock:
            self._prune_unlocked()
            return {
                "version": GATE_VERSION,
                "mode": self.mode,
                "ttlSeconds": self.ttl_seconds,
                "allowanceSeconds": self.allowance_seconds,
                # Only meaningful in `destructive` mode: the other modes gate by
                # annotation, so naming a fixed list there would be a lie.
                "gatedTools": (sorted(self.destructive_tools)
                               if self.mode == "destructive" else []),
                "gateScope": GATE_SCOPES.get(self.mode, "nothing"),
                "destructiveToolCount": len(self.destructive_tools),
                "pending": [rec.public() for rec in self._pending.values()],
                "pendingCount": len(self._pending),
                "recentDecisions": list(self._history),
                "allowances": [
                    {"tool": key.split("|", 1)[0], "principal": key.split("|", 1)[1],
                     "secondsRemaining": max(0, round(exp - time.time(), 1))}
                    for key, exp in sorted(self._allowances.items()) if exp > time.time()
                ],
                "counters": dict(self.counters),
            }

    # -- policy --------------------------------------------------------------

    def requires(self, tool: str, annotations: dict = None, arguments: dict = None) -> bool:
        if not self.enabled:
            return False
        annotations = annotations or {}
        if self.mode == "all":
            return True
        if self.mode == "write":
            return not bool(annotations.get("readOnlyHint"))
        if self.mode == "destructive" or self.mode not in GATE_MODES:
            return tool in self.destructive_tools or bool(annotations.get("destructiveHint"))
        return False

    def allowance_active(self, tool: str, principal: str) -> bool:
        key = f"{tool}|{principal}"
        with self._lock:
            self._prune_unlocked()
            exp = self._allowances.get(key)
            if exp and exp > time.time():
                self.counters["allowed_by_window"] += 1
                return True
            if exp:
                self._allowances.pop(key, None)
            return False

    def grant_allowance(self, tool: str, principal: str, seconds: float = None):
        if self.allowance_seconds <= 0:
            return
        window = seconds or self.allowance_seconds
        with self._lock:
            self._allowances[f"{tool}|{principal}"] = time.time() + max(0.0, min(window, MAX_ALLOWANCE_SECONDS))

    # -- request lifecycle ---------------------------------------------------

    def request(self, tool: str, arguments: dict, principal: str, danger_keys=None) -> Confirmation:
        digest = canonical_digest(tool, arguments)
        nonce = secrets.token_hex(16)
        rec = Confirmation(tool, arguments, principal or "owner", nonce, digest,
                           self.ttl_seconds, danger_keys)
        with self._lock:
            self._prune_unlocked()
            while len(self._pending) >= MAX_PENDING:
                oldest_id = next(iter(self._pending))
                stale = self._pending.pop(oldest_id, None)
                if stale:
                    stale.state = _STATE_EXPIRED
                    stale.event.set()
            self._pending[rec.id] = rec
            self.counters["requested"] += 1
        self._emit("mcp.confirm.requested", {
            "requestId": rec.id, "tool": tool, "principal": rec.principal,
            "expiresAt": Confirmation._iso(rec.expires),
        })
        return rec

    def take_decision(self, request_id: str, principal: str = None) -> dict:
        """Consume an already-recorded decision (out-of-band approval, then retry)."""
        with self._lock:
            rec = self._decisions.get(request_id or "")
            if not rec:
                return None
            if rec["consumed"]:
                raise GateError("replayed", "That confirmation has already been used.")
            if time.time() > rec["usableUntil"]:
                self._decisions.pop(request_id, None)
                raise GateError("expired", "The confirmation window closed; re-issue the call.")
            if principal is not None and self._principal_tag(rec.get("principal")) != self._principal_tag(principal):
                raise GateError("principal_mismatch", "That confirmation belongs to a different caller.")
            rec["consumed"] = True
            if rec.get("jti"):
                # Spending the nonce here (not at resolve time) is what makes
                # "one out-of-band approval, one retry" true: the retry after an
                # operator's answer is allowed exactly once, then the token dies.
                self._consumed[rec["jti"]] = rec["usableUntil"]
            self._decisions.pop(request_id, None)
            return dict(rec)

    def pending(self, request_id: str) -> Confirmation:
        with self._lock:
            return self._pending.get(request_id or "")

    def state_token(self, rec: Confirmation) -> str:
        """Opaque, integrity-protected `requestState` (SEP-2322 requirement)."""
        payload = {
            "v": 1,
            "id": rec.id,
            "sub": self._principal_tag(rec.principal),
            "aud": "tools/call",
            "iat": round(rec.created, 3),
            "exp": round(rec.expires, 3),
            "jti": rec.nonce,
            "dig": rec.digest,
        }
        body = _b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        tag = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()[:16]
        return f"{body}.{_b64encode(tag)}"

    def parse_state(self, token: str) -> dict:
        """Verify a client-echoed `requestState`. Raises GateError on anything odd."""
        if not token or not isinstance(token, str) or "." not in token:
            raise GateError("malformed_state", "requestState is missing or malformed.")
        body, tag = token.rsplit(".", 1)
        try:
            expected = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()[:16]
            provided = _b64decode(tag)
        except Exception as exc:  # noqa: BLE001 - base64 errors are user input
            raise GateError("malformed_state", f"requestState could not be decoded: {exc}") from exc
        if not hmac.compare_digest(expected, provided):
            raise GateError("bad_signature", "requestState signature does not match this bridge.")
        try:
            payload = json.loads(_b64decode(body).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise GateError("malformed_state", f"requestState payload is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise GateError("malformed_state", "requestState payload is not an object.")
        if time.time() > float(payload.get("exp") or 0):
            raise GateError("expired", "The confirmation window closed; re-issue the call.")
        with self._lock:
            self._prune_unlocked()
            if payload.get("jti") in self._consumed:
                raise GateError("replayed", "This confirmation token was already used.")
        return payload

    def resolve(self, request_id: str, responses, principal: str = None,
                 source: str = "client", expected_digest: str = None) -> dict:
        """Apply a decision (approve/deny) to a pending request, in-band or not."""
        rec = self.pending(request_id)
        if rec is None:
            raise GateError("unknown_request", f"No pending confirmation with id {request_id!r}.")
        with self._lock:
            if rec.state != _STATE_PENDING:
                return {"requestId": rec.id, "state": rec.state, "alreadyDecided": True,
                        "note": rec.note}
            if rec.expires < time.time():
                rec.state = _STATE_EXPIRED
                self.counters["expired"] += 1
                self._forget_unlocked(rec)
                rec.event.set()
                raise GateError("expired", "The confirmation window closed; re-issue the call.")
            if source == "client" and self._principal_tag(rec.principal) != self._principal_tag(principal):
                raise GateError("principal_mismatch",
                                "requestState was issued to a different caller than the one retrying.")
            if expected_digest and expected_digest != rec.digest:
                raise GateError("digest_mismatch",
                                "The retried call is not the call this confirmation was for.")

        approved, remember, note = self._read_responses(responses)
        with self._lock:
            rec.answer = "approve" if approved else "deny"
            rec.remember = bool(remember) and approved
            rec.note = note or None
            rec.state = _STATE_APPROVED if approved else _STATE_DENIED
            rec.decision_source = source
            self.counters["approved" if approved else "denied"] += 1
            if rec.remember:
                self.grant_allowance(rec.tool, rec.principal)
            self._forget_unlocked(rec)
            if source == "client":
                # The retry that carried this answer is the token's last use.
                self._consumed[rec.nonce] = rec.expires
            self._decisions[rec.id] = {
                "requestId": rec.id,
                "jti": rec.nonce,
                "approved": approved,
                "note": rec.note,
                "source": source,
                "decidedAt": time.time(),
                "principal": rec.principal,
                "usableUntil": rec.expires + 60.0,
                "consumed": False,
            }
            self._history.insert(0, rec.public())
            del self._history[MAX_DECISION_HISTORY:]
        rec.event.set()
        self._emit("mcp.confirm." + ("approved" if approved else "denied"), {
            "requestId": rec.id, "tool": rec.tool, "source": source,
            "principal": rec.principal, "remember": rec.remember, "note": rec.note,
        })
        return {"requestId": rec.id, "state": rec.state, "tool": rec.tool,
                "remembered": rec.remember, "note": rec.note}

    def expire_stale(self) -> int:
        """Sweep requests nobody answered (also called by the bridge reaper)."""
        swept = 0
        with self._lock:
            now = time.time()
            for rid, rec in list(self._pending.items()):
                if rec.expires < now:
                    self._pending.pop(rid, None)
                    rec.state = _STATE_EXPIRED
                    self.counters["expired"] += 1
                    rec.event.set()
                    swept += 1
            if swept:
                self._emit("mcp.confirm.expired", {"swept": swept})
        return swept

    def cancel(self, request_id: str, reason: str = "cancelled") -> bool:
        rec = self.pending(request_id)
        if rec is None:
            return False
        with self._lock:
            if rec.state != _STATE_PENDING:
                return False
            self._forget_unlocked(rec)
            # The token dies with the request, so a retry cannot reuse it.
            self._consumed[rec.nonce] = rec.expires
            rec.state = _STATE_DENIED
            rec.answer = "deny"
            rec.decision_source = "system"
            rec.note = reason
        rec.event.set()
        return True

    # -- waits ---------------------------------------------------------------

    def wait(self, rec: Confirmation, timeout: float = None) -> bool:
        """Block until the request is answered. True when answered in time."""
        remaining = timeout if timeout is not None else (rec.expires - time.time())
        return rec.event.wait(timeout=max(0.05, min(remaining, self.ttl_seconds)))

    # -- internals -----------------------------------------------------------

    def _principal_tag(self, principal) -> str:
        return hashlib.sha256(str(principal or "owner").encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def same_principal(a, b) -> bool:
        """Compare principals without exposing them: the tag is what a
        `requestState` carries, so this is the same test the gate applies to a
        retry."""
        return hashlib.sha256(str(a or "owner").encode("utf-8")).hexdigest()[:12] == \
            hashlib.sha256(str(b or "owner").encode("utf-8")).hexdigest()[:12]

    def _read_responses(self, responses):
        """Normalise an `inputResponses` map (or a flat dict) into a decision."""
        if responses is None:
            return False, False, None
        if isinstance(responses, bool):
            return responses, False, None
        if isinstance(responses, (str, int)):
            text = str(responses).strip().lower()
            return text in ("approve", "approved", "yes", "true", "1"), False, None
        if not isinstance(responses, dict):
            raise GateError("malformed_responses", "inputResponses must be an object.")
        inner = responses.get("confirm")
        # An ElicitResult-shaped answer: {"confirm": {"action": "accept", "content": {...}}}
        if isinstance(inner, dict) and ("action" in inner or "content" in inner):
            action = str(inner.get("action") or "").strip().lower()
            payload = inner.get("content") if isinstance(inner.get("content"), dict) else {}
            decision = str(payload.get("decision") or "").strip().lower()
            if action in ("decline", "cancel") or decision in ("deny", "decline"):
                return False, False, payload.get("note")
            approved = action == "accept" or decision == "approve"
            if "confirmed" in payload and not decision:
                approved = bool(payload.get("confirmed"))
            return approved, bool(payload.get("remember")), payload.get("note")
        # Flat convenience shape: {"approve": true, "remember": true, "note": "..."}
        approved = responses.get("approve")
        if approved is None:
            approved = responses.get("confirmed")
        if approved is None:
            approved = responses.get("decision")
        if isinstance(approved, str):
            approved = approved.strip().lower() in ("approve", "approved", "yes", "true", "1")
        return bool(approved), bool(responses.get("remember")), responses.get("note")

    def _forget_unlocked(self, rec: Confirmation):
        self._pending.pop(rec.id, None)

    def _prune_unlocked(self):
        now = time.time()
        for rid, rec in list(self._pending.items()):
            if rec.expires < now:
                self._pending.pop(rid, None)
                rec.state = _STATE_EXPIRED
                self.counters["expired"] += 1
                rec.event.set()
                self._history.insert(0, rec.public())
                del self._history[MAX_DECISION_HISTORY:]
        for nonce, exp in list(self._consumed.items()):
            if exp < now:
                self._consumed.pop(nonce, None)
        for rid, decision in list(self._decisions.items()):
            if decision.get("usableUntil", 0) < now or decision.get("consumed"):
                self._decisions.pop(rid, None)
        if len(self._decisions) > MAX_PENDING:
            for rid in sorted(self._decisions, key=lambda k: self._decisions[k]["decidedAt"])[:
                    len(self._decisions) - MAX_PENDING]:
                self._decisions.pop(rid, None)
        for key, exp in list(self._allowances.items()):
            if exp <= now:
                self._allowances.pop(key, None)
        if len(self._history) > MAX_DECISION_HISTORY:
            del self._history[MAX_DECISION_HISTORY:]

    def _emit(self, kind: str, detail: dict):
        if not self.hook:
            return
        try:
            self.hook(kind, detail)
        except Exception:  # noqa: BLE001 - observability must never break a call
            pass


def danger_keys_in(arguments: dict) -> list:
    """Argument names that look like they carry destructive intent (for the form)."""
    if not isinstance(arguments, dict):
        return []
    keys = []
    for key, value in arguments.items():
        low = str(key).lower()
        if low in ("path", "root", "id", "code", "source", "target", "replaceinstance",
                   "confirm", "autofixunanchored", "snapfloating", "force", "delete",
                   "dryrun", "players", "module", "action", "mode"):
            keys.append(key)
    return keys
