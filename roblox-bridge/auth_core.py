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

AUTH_VERSION = "5.3.0"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "bridge_state"

ROLE_RANK = {"read": 1, "write": 2, "admin": 3}
ROLE_DESCRIPTIONS = {
    "read": "Inspect only: state, peers, survey, script reads, telemetry.",
    "write": "Everything read can do, plus Studio mutations, jobs and bus writes.",
    "admin": "Full control: token management, tunnel control, queue clearing, audit.",
}

# Paths that require a specific role regardless of the HTTP method.
READ_ONLY_PATHS = {
    "/api/health", "/api/state", "/api/peers", "/api/jobs", "/api/agent-guide",
    "/api/metrics", "/api/audit", "/api/events", "/api/ops", "/api/self-test",
}
ADMIN_PATHS = {"/api/tokens", "/api/audit/verify"}


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
