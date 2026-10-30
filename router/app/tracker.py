"""Request log. Metadata only by default: no prompt or response text is stored.

The debug flag (local development only) stores text with personal data patterns redacted,
and deletes it after the retention period.
"""
import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

_REDACTIONS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "[PAN]"),
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), "[CARD_OR_ACCOUNT]"),
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"), "[AADHAAR]"),
    (re.compile(r"(?:\+91[ -]?)?\b[6-9]\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b\d{9,18}\b"), "[ACCOUNT]"),
]


def redact(text: str) -> str:
    """Masks emails, PAN, card/account, Aadhaar and Indian phone numbers."""
    for pattern, label in _REDACTIONS:
        text = pattern.sub(label, text)
    return text


def hash_client(identity: str) -> str:
    """Logs keep a short one-way hash of the caller (IP or key), never the raw value."""
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  day TEXT NOT NULL,
  client_hash TEXT,
  policy_version TEXT,
  mode TEXT,
  category TEXT,
  difficulty TEXT,
  confidence REAL,
  routed_to TEXT,
  reasoning_effort TEXT,
  reason TEXT,
  forced INTEGER DEFAULT 0,
  compare INTEGER DEFAULT 0,
  downgraded INTEGER DEFAULT 0,
  fallbacks_used INTEGER DEFAULT 0,
  calls_made INTEGER DEFAULT 0,
  attempts_json TEXT,
  status TEXT NOT NULL,
  error_category TEXT,
  schema_valid INTEGER,
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  estimated_cost_usd REAL DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  reference_cost_usd REAL,
  latency_ms INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_requests_day ON requests(day);
CREATE TABLE IF NOT EXISTS debug_text (
  request_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  prompt_redacted TEXT,
  response_redacted TEXT
);
"""

COLUMNS = ["id", "client_hash", "policy_version", "mode", "category", "difficulty", "confidence", "routed_to",
           "reasoning_effort", "reason", "forced", "compare", "downgraded", "fallbacks_used", "calls_made", "attempts_json",
           "status", "error_category", "schema_valid", "prompt_tokens", "completion_tokens",
           "estimated_cost_usd", "cost_usd", "reference_cost_usd", "latency_ms"]


class Tracker:
    def __init__(self, path: str, debug_text: bool = False, retention_days: int = 7, now=_utcnow):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._lock = threading.Lock()
        self.debug_text = debug_text
        self.retention_days = retention_days
        self._now = now
        self.purge_debug_text()

    def _migrate(self) -> None:
        """Adds columns introduced after a log database was first created (keeps old rows)."""
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(requests)")}
        for column, sql_type in (("compare", "INTEGER DEFAULT 0"),):
            if column not in have:
                self._conn.execute(f"ALTER TABLE requests ADD COLUMN {column} {sql_type}")
        self._conn.commit()

    def log(self, record: dict, prompt_text: str | None = None, response_text: str | None = None) -> None:
        now = self._now()
        row = {c: record.get(c) for c in COLUMNS}
        if isinstance(row["attempts_json"], (list, dict)):
            row["attempts_json"] = json.dumps(row["attempts_json"])
        cols = COLUMNS + ["created_at", "day"]
        values = [row[c] for c in COLUMNS] + [now.isoformat(), now.date().isoformat()]
        with self._lock:
            self._conn.execute(f"INSERT INTO requests ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", values)
            if self.debug_text and (prompt_text or response_text):
                self._conn.execute(
                    "INSERT OR REPLACE INTO debug_text VALUES (?,?,?,?)",
                    (record["id"], now.isoformat(), redact(prompt_text or ""), redact(response_text or "")))
            self._conn.commit()

    def purge_debug_text(self) -> int:
        cutoff = (self._now() - timedelta(days=self.retention_days)).isoformat()
        with self._lock:
            cur = self._conn.execute("DELETE FROM debug_text WHERE created_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def spent_today(self) -> float:
        day = self._now().date().isoformat()
        with self._lock:
            (total,) = self._conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM requests WHERE day = ?",
                                          (day,)).fetchone()
        return float(total)

    def get(self, request_id: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
            row = cur.fetchone()
            names = [d[0] for d in cur.description]
        return dict(zip(names, row)) if row else None

    def stats(self) -> dict:
        with self._lock:
            all_rows = self._conn.execute(
                "SELECT status, routed_to, fallbacks_used, cost_usd, reference_cost_usd, latency_ms, error_category, "
                "compare, category FROM requests").fetchall()
        # Compare runs are the visitor asking the reference model on purpose: counted as spend,
        # but kept out of routing figures so they do not distort savings or the model mix.
        rows = [r for r in all_rows if not r[7]]
        compare_spend = sum(r[3] or 0 for r in all_rows if r[7])
        total = len(rows)
        ok = [r for r in rows if r[0] == "ok"]
        latencies = sorted(r[5] for r in ok)

        def pct(p):
            if not latencies:
                return None
            return latencies[min(len(latencies) - 1, int(round(p * (len(latencies) - 1))))]

        by_model: dict[str, int] = {}
        for r in ok:
            by_model[r[1]] = by_model.get(r[1], 0) + 1
        errors: dict[str, int] = {}
        for r in rows:
            if r[0] != "ok":
                errors[r[0]] = errors.get(r[0], 0) + 1
        cost = sum(r[3] or 0 for r in rows)
        reference = sum(r[4] or 0 for r in ok)
        by_category: dict[str, int] = {}
        for r in ok:
            key = r[8] or "unknown"
            by_category[key] = by_category.get(key, 0) + 1
        return {
            "requests": total,
            "answered_pct": round(100 * len(ok) / total, 1) if total else None,
            "total_cost_usd": round(cost, 6),
            "avg_cost_per_answer_usd": round(sum(r[3] or 0 for r in ok) / len(ok), 8) if ok else None,
            "compare_runs": sum(1 for r in all_rows if r[7]),
            "compare_spend_usd": round(compare_spend, 6),
            "reference_cost_usd": round(reference, 6),
            "spent_today_usd": round(self.spent_today(), 6),
            "latency_p50_ms": pct(0.5),
            "latency_p95_ms": pct(0.95),
            "fallback_rate_pct": round(100 * sum(1 for r in ok if (r[2] or 0) > 0) / len(ok), 1) if ok else None,
            "requests_by_model": by_model,
            "requests_by_category": by_category,
            "errors_by_status": errors,
        }
