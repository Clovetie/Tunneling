#!/usr/bin/env python3
"""
auth_core.py — Scoped access tokens and per-token rate limiting for the bridge.

The single `bridge.token` you already have keeps working unchanged: it is treated
as the full-privilege owner credential. On top of that you can mint named,
expiring, scoped tokens so that:

  * a read-only inspector agent cannot mutate a place,
  * a teammate's CI can push scripts but cannot mint tokens or start tunnels,
  * a demo viewer can watch telemetry without touching Studio,
  * a runaway agent gets throttled instead of flooding the job queue.

Only token *hashes* are stored, and only the first 8 characters of a token are
kept for display. Tokens are shown in full exactly once, when minted.

Zero dependencies.
"""

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

AUTH_VERSION = "6.1.0"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "bridge_state"

#: The demo credential that ships inside `ArenaBridge.lua` so an unconfigured
#: plugin still compiles. It is **not** a usable secret: `TokenStore.authenticate`
#: refuses it, and `setup.py` refuses to bake a plugin with it. v6.1.0 (P0-S5)
#: made this the single definition — it used to be copy-pasted into four files,
#: and `server.py` authenticated it happily.
PLACEHOLDER_TOKEN = "arena-demo-7f3a"

ROLE_RANK = {"read": 1, "write": 2, "admin": 3}
ROLE_DESCRIPTIONS = {
    "read": "Inspect only: state, peers, survey, script reads, telemetry.",
    "write": "Everything read can do, plus Studio mutations, jobs and bus writes.",
    "admin": "Full control: token management, tunnel control, queue clearing, audit.",
}

# Paths that require a specific role regardless of the HTTP method.
# v6.1.0 (P0-S6): these two sets were dead code — nothing read them. They are now
# the single written-down statement of route policy, consulted by
# `server.py`'s `_route_scope()` on every request, so a new `/api/*` route that
# forgets `_require(...)` still gets its declared scope enforced.
#: Routes whose floor is `read`. Deliberately *not* listed here:
#:   * `/api/kit/catalog` — its scope depends on the query (`item=`, `section=`,
#:     `format=full` need `read`; bare metadata is public because the kit is CC0).
#:     A blanket floor would either leak source or break the public gallery, so
#:     the handler decides and `test_declared_route_policy_is_enforced` pins it.
#:   * `/api/kit/image|audio|model|thumbnail|procedural|synth-audio|viewport-preview`
#:     — traversal-safe binary serving that never calls `_require`.
READ_ONLY_PATHS = {
    "/api/state", "/api/peers", "/api/jobs", "/api/agent-guide",
    "/api/metrics", "/api/audit", "/api/events", "/api/ops", "/api/self-test",
    "/api/plugin-status", "/api/sse-ticket", "/api/subscriptions", "/api/bus",
    "/api/snapshots", "/api/confirmations", "/api/tunnel",
}
#: `/api/audit?verify=1` re-checks the audit hash chain and discloses nothing
#: that `/api/audit` itself does not, so it stays a `read` action rather than
#: getting its own admin path — a declaration for a route the server does not
#: dispatch is worse than no declaration at all.
ADMIN_PATHS = {"/api/tokens", "/api/plugin/download"}

#: Routes that are public by design: they carry no credential and no secret.
#: `/` and `/static/*` are the dashboard shell (its data comes from `/api/*`),
#: `/.well-known/*` is MCP discovery, and `/api/health` is a liveness probe that
#: the tunnel, CI and a browser can hit before any token exists. The two
#: `/api/kit/*` search routes serve the CC0 asset/icon *index* (names, licences,
#: sizes, thumbnails) and never call `_require`; the dashboard gallery renders
#: from them before it has a token, so declaring a `read` floor there would have
#: advertised a rule the handler does not enforce.
PUBLIC_PATHS = {"/", "/static", "/api/health", "/.well-known",
                "/api/kit/cloud-icons", "/api/kit/assets"}

#: Routes whose floor depends on the query or on who is asking, so they declare
#: nothing here and enforce it in the handler:
#:   * `/api/kit/catalog` — `item=` or `section=` need `read` (that is where kit
#:     *source* comes out); a category listing is name/path/bytes only and stays
#:     public because the kit is CC0.
#:   * `/api/local-boot` — always 200 so the page can render, but the credential
#:     field is empty unless the caller is admin *and* local.
#:   * `/api/kit/image|audio|model|thumbnail|procedural|synth-audio|viewport-preview`
#:     — traversal-safe binary serving that never calls `_require`.
#: `test_declared_route_policy_is_enforced_and_not_dead_code` walks every route
#: in `TestV61SecurityHardening.ROUTES` as an anonymous cross-site caller and
#: pins the declared floor, so a handler cannot drift away from this table.
ROUTE_DECIDES_PATHS = {"/api/kit/catalog", "/api/kit/image", "/api/kit/audio",
                       "/api/kit/thumbnail", "/api/kit/procedural",
                       "/api/kit/synth-audio", "/api/kit/viewport-preview",
                       "/api/local-boot", "/mcp"}


def path_scope(path: str):
    """Declared minimum role for `path`, or None when the route sets its own.

    Longest-prefix match across all three policy sets, so `/api/jobs/<id>`
    inherits `/api/jobs` and `/static/dashboard.js` inherits `/static`. A public
    prefix wins outright: listing a route in both `PUBLIC_PATHS` and
    `READ_ONLY_PATHS` used to make this function advertise a `read` floor the
    handler never enforced, which is worse than either answer alone.

    Used as a floor: a handler may require *more*, never less.
    """
    clean = (path or "/").split("?", 1)[0]

    def _match(candidate: str) -> bool:
        stripped = candidate.rstrip("/")
        if not stripped:                 # "/" names the dashboard shell only
            return clean in ("/", "")
        return clean == candidate or clean == stripped or clean.startswith(stripped + "/")

    if any(_match(candidate) for candidate in PUBLIC_PATHS):
        return None
    best = None
    best_len = -1
    for candidate in READ_ONLY_PATHS:
        if _match(candidate) and len(candidate) > best_len:
            best, best_len = "read", len(candidate)
    for candidate in ADMIN_PATHS:
        if _match(candidate) and len(candidate) > best_len:
            best, best_len = "admin", len(candidate)
    return best


def _now() -> float:
    return time.time()


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TokenRecord:
    __slots__ = ("id", "label", "role", "created", "expires", "revoked", "last_used",
                 "uses", "rate_limit", "burst", "bucket", "bucket_ts", "token_hash", "prefix")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))

    def view(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "prefix": self.prefix,
            "created": self.created,
            "expires": self.expires,
            "revoked": bool(self.revoked),
            "lastUsed": self.last_used,
            "uses": self.uses,
            "rateLimitPerSecond": self.rate_limit,
            "burst": self.burst,
        }

    def active(self, now: float = None) -> bool:
        now = now or _now()
        if self.revoked:
            return False
        if self.expires and self.expires < now:
            return False
        return True


class TokenStore:
    """Hashed token registry with role scopes, expiry, revocation and rate limits."""

    def __init__(self, path=None, primary_token: str = "", persist: bool = True, default_rate: float = 20.0):
        self.path = Path(path) if path else DEFAULT_STATE_DIR / "tokens.json"
        self.persist = bool(persist)
        self.lock = threading.RLock()
        self.default_rate = float(default_rate)
        self.records = {}       # token id -> TokenRecord
        self.by_hash = {}       # sha256(token) -> token id
        self.primary = None     # TokenRecord for the bridge.token owner credential
        if primary_token:
            self.primary = TokenRecord(
                id="primary", label="owner (bridge.token)", role="admin",
                created=_now(), expires=None, revoked=False, last_used=None, uses=0,
                rate_limit=0.0, burst=0, bucket=0.0, bucket_ts=_now(),
                token_hash=_hash(primary_token), prefix=primary_token[:8],
            )
            self.by_hash[self.primary.token_hash] = "primary"
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        if not self.persist or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for row in raw.get("tokens", []):
            try:
                rec = TokenRecord(
                    id=row["id"], label=row.get("label") or "", role=row.get("role") or "read",
                    created=row.get("created") or _now(), expires=row.get("expires"),
                    revoked=bool(row.get("revoked")), last_used=row.get("lastUsed"), uses=int(row.get("uses") or 0),
                    rate_limit=float(row.get("rateLimitPerSecond") or 0.0), burst=float(row.get("burst") or 0.0),
                    bucket=float(row.get("burst") or 0.0), bucket_ts=_now(),
                    token_hash=row["hash"], prefix=row.get("prefix") or "",
                )
            except (KeyError, TypeError, ValueError):
                continue
            self.records[rec.id] = rec
            self.by_hash[rec.token_hash] = rec.id

    def _save(self):
        if not self.persist:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": AUTH_VERSION,
                "saved": _now(),
                "tokens": [
                    dict(r.view(), hash=r.token_hash)
                    for r in self.records.values()
                ],
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.chmod(0o600)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # -- authentication ------------------------------------------------------

    def authenticate(self, raw: str, consume_rate: bool = True):
        """Return (record, rate_info) or (None, None) when the token is invalid."""
        if not raw:
            return None, None
        # v6.1.0 (P0-S5): the demo literal that ships in ArenaBridge.lua is never
        # a credential, even if an operator points --token at it by accident.
        if raw.strip() == PLACEHOLDER_TOKEN:
            return None, None
        token_hash = _hash(raw)
        with self.lock:
            rec_id = self.by_hash.get(token_hash)
            if not rec_id:
                return None, None
            rec = self.primary if rec_id == "primary" else self.records.get(rec_id)
            if not rec or not rec.active():
                return None, None
            rate_info = self._rate_check_unlocked(rec) if consume_rate else {"allowed": True, "retryAfter": 0}
            if not rate_info["allowed"]:
                return rec, rate_info
            rec.last_used = _now()
            rec.uses = int(rec.uses or 0) + 1
            return rec, rate_info

    def _rate_check_unlocked(self, rec: TokenRecord) -> dict:
        rate = float(rec.rate_limit or 0.0)
        burst = float(rec.burst or rate * 2 or 0.0)
        if rate <= 0:
            return {"allowed": True, "retryAfter": 0, "remaining": None, "limit": 0}
        now = _now()
        elapsed = max(0.0, now - float(rec.bucket_ts or now))
        tokens = min(burst, float(rec.bucket or 0.0) + elapsed * rate)
        if tokens < 1.0:
            deficit = 1.0 - tokens
            retry = max(1, int(deficit / rate) + 1)
            rec.bucket = tokens
            rec.bucket_ts = now
            return {"allowed": False, "retryAfter": retry, "remaining": 0, "limit": rate}
        rec.bucket = tokens - 1.0
        rec.bucket_ts = now
        return {"allowed": True, "retryAfter": 0, "remaining": int(rec.bucket), "limit": rate}

    # -- management ----------------------------------------------------------

    def create(self, label: str, role: str = "write", ttl_seconds: int = None, rate_limit: float = None, burst: float = None) -> dict:
        role = (role or "write").lower()
        if role not in ROLE_RANK:
            raise ValueError(f"role must be one of: {', '.join(ROLE_RANK)}")
        raw = "arb_" + secrets.token_urlsafe(30)
        rate = self.default_rate if rate_limit is None else float(rate_limit)
        with self.lock:
            rec = TokenRecord(
                id="tok-" + secrets.token_hex(6),
                label=(label or "unnamed")[:120],
                role=role,
                created=_now(),
                expires=(_now() + int(ttl_seconds)) if ttl_seconds else None,
                revoked=False,
                last_used=None,
                uses=0,
                rate_limit=rate,
                burst=float(burst if burst is not None else max(rate * 2, 1.0)),
                bucket=float(burst if burst is not None else max(rate * 2, 1.0)),
                bucket_ts=_now(),
                token_hash=_hash(raw),
                prefix=raw[:8],
            )
            self.records[rec.id] = rec
            self.by_hash[rec.token_hash] = rec.id
            self._save()
            view = rec.view()
            view["token"] = raw
            view["note"] = "Store this token now — only its hash and prefix are retained."
            return view

    def revoke(self, token_id: str) -> dict:
        with self.lock:
            if token_id == "primary":
                raise ValueError("The owner credential (bridge.token) cannot be revoked here; rotate it instead.")
            rec = self.records.get(token_id)
            if not rec:
                raise KeyError(f"Unknown token id: {token_id!r}")
            rec.revoked = True
            self._save()
            return rec.view()

    def list(self) -> dict:
        with self.lock:
            rows = []
            if self.primary:
                rows.append(self.primary.view())
            rows += [r.view() for r in self.records.values()]
        rows.sort(key=lambda r: (r["revoked"], r["created"] or 0))
        return {
            "tokens": rows,
            "count": len(rows),
            "active": sum(1 for r in rows if not r["revoked"] and (not r["expires"] or r["expires"] > _now())),
            "roles": ROLE_DESCRIPTIONS,
        }

    def owner_token(self) -> str:
        """Only used by tests/first-run bootstrap; never exposed over HTTP."""
        return ""

    @staticmethod
    def role_allows(role: str, required: str) -> bool:
        return ROLE_RANK.get(role or "read", 0) >= ROLE_RANK.get(required or "read", 0)

    def handle(self, payload: dict) -> dict:
        action = (payload or {}).get("action", "list")
        p = payload or {}
        if action == "list":
            return self.list()
        if action == "create":
            return self.create(
                p.get("label") or "unnamed",
                p.get("role") or "write",
                p.get("ttlSeconds"),
                p.get("rateLimitPerSecond"),
                p.get("burst"),
            )
        if action == "revoke":
            return self.revoke(p.get("id") or p.get("tokenId"))
        if action == "roles":
            return {"roles": ROLE_DESCRIPTIONS, "rank": ROLE_RANK}
        raise ValueError("Unknown token action. Use list, create, revoke or roles.")
