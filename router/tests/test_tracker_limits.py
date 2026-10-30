from datetime import datetime, timedelta, timezone

import pytest

from app.limits import RateLimited, RateLimiter
from app.tracker import Tracker, hash_client, redact


@pytest.mark.parametrize("text, expected", [
    ("mail me at ravi.k@example.co.in", "mail me at [EMAIL]"),
    ("PAN ABCDE1234F please", "PAN [PAN] please"),
    ("card 4111 1111 1111 1111 declined", "card [CARD_OR_ACCOUNT] declined"),
    ("card 4111-1111-1111-1111", "card [CARD_OR_ACCOUNT]"),
    ("Aadhaar 2345 6789 0123", "Aadhaar [AADHAAR]"),
    ("call +91 9876543210", "call [PHONE]"),
    ("a/c 123456789012345", "a/c [CARD_OR_ACCOUNT]"),
    ("a/c 50100123456", "a/c [ACCOUNT]"),
    ("EMI of 25000 for 36 months", "EMI of 25000 for 36 months"),  # ordinary numbers stay
])
def test_redaction(text, expected):
    assert redact(text) == expected


def test_client_identity_is_hashed():
    h = hash_client("ip:203.0.113.9")
    assert len(h) == 16 and "203" not in h


def _record(rid, cost=0.001, status="ok", model="gpt-4.1-mini", fallbacks=0, latency=900):
    return {"id": rid, "status": status, "routed_to": model, "cost_usd": cost, "reference_cost_usd": cost * 10,
            "fallbacks_used": fallbacks, "latency_ms": latency, "attempts_json": [{"model": model}]}


def test_metadata_only_by_default(tmp_path):
    t = Tracker(str(tmp_path / "l.db"))
    t.log(_record("r1"), prompt_text="my PAN is ABCDE1234F", response_text="noted")
    rows = t._conn.execute("SELECT COUNT(*) FROM debug_text").fetchone()[0]
    assert rows == 0
    stored = t.get("r1")
    assert "ABCDE1234F" not in str(stored) and stored["routed_to"] == "gpt-4.1-mini"


def test_debug_mode_stores_redacted_text_and_purges_old_rows(tmp_path):
    now = [datetime(2026, 11, 1, tzinfo=timezone.utc)]
    t = Tracker(str(tmp_path / "l.db"), debug_text=True, retention_days=7, now=lambda: now[0])
    t.log(_record("r1"), prompt_text="my PAN is ABCDE1234F", response_text="ok")
    text = t._conn.execute("SELECT prompt_redacted FROM debug_text").fetchone()[0]
    assert text == "my PAN is [PAN]"
    now[0] += timedelta(days=8)
    assert t.purge_debug_text() == 1


def test_spent_today_and_stats(tmp_path):
    now = [datetime(2026, 11, 1, 10, tzinfo=timezone.utc)]
    t = Tracker(str(tmp_path / "l.db"), now=lambda: now[0])
    t.log(_record("a", cost=0.002, latency=1000))
    t.log(_record("b", cost=0.003, fallbacks=1, latency=3000, model="gpt-4.1"))
    t.log(_record("c", cost=0.0, status="all_models_failed"))
    assert t.spent_today() == pytest.approx(0.005)
    s = t.stats()
    assert s["requests"] == 3 and s["answered_pct"] == pytest.approx(66.7)
    assert s["requests_by_model"] == {"gpt-4.1-mini": 1, "gpt-4.1": 1}
    assert s["fallback_rate_pct"] == 50.0 and s["errors_by_status"] == {"all_models_failed": 1}
    assert s["latency_p50_ms"] in (1000, 3000) and s["latency_p95_ms"] == 3000
    now[0] += timedelta(days=1)
    assert t.spent_today() == 0


def test_rate_limit_per_minute_and_window():
    clock = [0.0]
    rl = RateLimiter(per_minute=2, per_day=100, clock=lambda: clock[0])
    rl.check("a")
    rl.check("a")
    with pytest.raises(RateLimited) as e:
        rl.check("a")
    assert e.value.code == "rate_limited"
    rl.check("b")  # other clients are unaffected
    clock[0] += 61
    rl.check("a")


def test_rate_limit_per_day_resets_next_day():
    clock = [datetime(2026, 11, 1, 23, 50, tzinfo=timezone.utc).timestamp()]
    rl = RateLimiter(per_minute=100, per_day=2, clock=lambda: clock[0])
    rl.check("a")
    rl.check("a")
    with pytest.raises(RateLimited) as e:
        rl.check("a")
    assert e.value.code == "daily_limit_reached"
    clock[0] += 20 * 60
    rl.check("a")
