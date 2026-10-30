"""Demo mode runs the real routing path with scripted answers."""
from fastapi.testclient import TestClient

from app.demo import DemoProvider
from app.main import build_service, create_app
from conftest import FakeClock


def _client(settings):
    s = settings.model_copy(update={"demo_mode": True})
    clock = FakeClock()
    app = create_app(s, build_service(s, None, clock, clock.sleep))
    return TestClient(app)


def _ask(client, text):
    return client.post("/v1/chat/completions", json={"model": "auto", "messages": [{"role": "user", "content": text}]})


def test_demo_answers_by_topic_and_reports_demo_mode(settings):
    client = _client(settings)
    assert client.get("/api/health").json()["demo_mode"] is True
    body = _ask(client, "I was charged twice for one purchase").json()
    assert "card_payment_charged_twice" in body["choices"][0]["message"]["content"]
    assert body["router"]["routed_to"] == "gpt-4.1-mini" and body["router"]["cost_usd"] > 0


def test_outage_tag_shows_retry_then_fallback(settings):
    body = _ask(_client(settings), "EMI on 10 lakh at 10% for 5 years #outage").json()
    r = body["router"]
    assert r["routed_to"] == "gpt-5-mini-low" and r["fallbacks_used"] == 1
    assert [(a["model"], a["outcome"], a["retry"]) for a in r["attempts"]] == [
        ("gpt-4.1-mini", "error", False), ("gpt-4.1-mini", "error", True), ("gpt-5-mini-low", "ok", False)]
    assert r["attempts"][0]["error_category"] == "rate_limit"


def test_reasoning_options_bill_thinking_tokens():
    provider = DemoProvider(sleep=lambda s: None)
    quick = provider(model="openai/gpt-4.1-mini", messages=[{"role": "user", "content": "hi"}])
    deep = provider(model="openai/gpt-5-mini", reasoning_effort="high", messages=[{"role": "user", "content": "hi"}])
    assert deep["usage"]["completion_tokens"] - quick["usage"]["completion_tokens"] == 320


def test_demo_costs_are_labelled_simulated(settings):
    client = _client(settings)
    meta = _ask(client, "Tag this complaint").json()["router"]
    assert meta["cost_basis"] == "simulated" and meta["price_checked"] == "2026-10-28"
    assert client.get("/api/stats").json()["cost_basis"] == "simulated"


def test_demo_prepayment_answer_matches_the_calculator():
    """The scripted answer once said 34 months; the exact figure is 51. Keep the script honest."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_gen.finance import prepayment_effect
    from app.demo import _answer_for
    r = prepayment_effect(4_000_000, 8.75, 240, after_payments=36, prepayment=500_000, keep="emi")
    text = _answer_for("I prepay Rs 5 lakh")
    assert f"{r.months_saved} months sooner" in text and f"after {r.total_months} months" in text
