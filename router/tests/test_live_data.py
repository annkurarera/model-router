"""Live-data questions (weather, news, today's prices) go to OpenAI's web-search models.

Only those questions: every other category keeps its chain, so no banking or code question
ever pays the per-search fee.
"""
import pytest
from fastapi.testclient import TestClient

from app.breaker import CircuitBreaker
from app.executor import ExecutionLimits, Executor, StopReason, _default_completion
from app.main import build_service, create_app
from app.registry import Registry, RegistryError
from app.rules import analyse
from conftest import FakeClock, FakeProvider


def cat(text):
    return analyse([{"role": "user", "content": text}]).category


@pytest.mark.parametrize("text", [
    "delhi temperature", "What is the weather in Mumbai?", "Latest RBI news",
    "What is the Sensex at right now?", "USD to INR exchange rate", "gold price in Delhi",
    "What is the current repo rate?", "Who won yesterday's match?",
])
def test_live_data_questions_are_detected(text):
    assert cat(text) == "live_data"


@pytest.mark.parametrize("text, expected", [
    ("My card was charged twice today, classify this complaint", "complaint"),
    ("EMI on Rs 10 lakh at the current rate of 9% for 5 years", "loan_maths"),
    ("Write SQL for today's card spend by merchant", "sql"),
    ("What is a current account?", "general"),
    ("Is my card blocked?", "general"),
])
def test_banking_and_code_questions_never_go_to_web_search(text, expected):
    assert cat(text) == expected


def test_signal_names_the_live_word():
    r = analyse([{"role": "user", "content": "delhi temperature"}])
    assert r.signals == ['needs live data: "temperature"']


def test_search_fee_is_added_per_call(registry):
    search = registry.get("gpt-5-search")
    assert search.web_search and search.per_call_usd == 0.01
    assert search.cost_usd(1000, 500) == pytest.approx(0.01 + (1000 * 1.25 + 500 * 10.0) / 1e6)
    assert search.worst_case_cost_usd(100, 800) < 0.05  # fits the default per-request limit


def test_retired_search_preview_models_are_off(registry):
    """Live finding: OpenAI retired the gpt-4o search-preview models (check_models, 2026-10-30)."""
    enabled = {o.id for o in registry.enabled()}
    assert "gpt-4o-mini-search" not in enabled and "gpt-4o-search" not in enabled


def test_ordinary_options_have_no_fee(registry):
    assert registry.get("gpt-4.1-mini").cost_usd(0, 0) == 0


def test_search_context_size_needs_web_search(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("options:\n  - {id: a, model: m, tier: mid, input_usd_per_mtok: 1, output_usd_per_mtok: 1, "
                    "search_context_size: low}\n")
    with pytest.raises(RegistryError, match="web_search"):
        Registry.load(path)


def test_executor_sends_web_search_options_and_no_temperature(make_executor):
    ex, provider = make_executor()
    ex.run_chain(["gpt-5-search"], [{"role": "user", "content": "delhi weather"}], 200, temperature=0.3)
    call = provider.calls[0]
    assert call["model"] == "openai/gpt-5-search-api"
    assert call["web_search_options"] == {"search_context_size": "low"}
    assert "temperature" not in call and "reasoning_effort" not in call


def test_real_litellm_accepts_the_search_parameters(registry):
    ex = Executor(registry, CircuitBreaker(), ExecutionLimits(),
                  lambda **kw: _default_completion(**kw, mock_response="About 20°C [source](https://example.com)"))
    r = ex.run_chain(["gpt-5-search"], [{"role": "user", "content": "delhi weather"}], 200)
    assert r.stop_reason == StopReason.OK and r.result.cost_usd > 0.01


@pytest.fixture
def client(settings):
    s = settings.model_copy(update={"routing_policy": "rules"})
    clock = FakeClock()
    provider = FakeProvider(clock)
    return TestClient(create_app(s, build_service(s, provider, clock, clock.sleep))), provider


def ask(client, text, **router):
    return client.post("/v1/chat/completions",
                       json={"model": "auto", "messages": [{"role": "user", "content": text}], "router": router})


def test_live_question_routes_to_search_and_is_measured_against_the_search_reference(client):
    c, provider = client
    meta = ask(c, "delhi temperature").json()["router"]
    assert meta["category"] == "live_data" and meta["routed_to"] == "gpt-5-search"
    assert meta["reference_model"] == "gpt-5-search"  # gpt-5-medium cannot search, so it is no yardstick
    assert meta["cost_usd"] > 0.01 and meta["reference_cost_usd"] == meta["cost_usd"]
    assert "web_search_options" in provider.calls[0]


def test_search_outage_still_gets_a_reply(settings):
    from conftest import err
    s = settings.model_copy(update={"routing_policy": "rules"})
    clock = FakeClock()
    provider = FakeProvider(clock, {"openai/gpt-5-search-api": [err("not_found")]})
    c = TestClient(create_app(s, build_service(s, provider, clock, clock.sleep)))
    meta = ask(c, "delhi temperature").json()["router"]
    assert meta["routed_to"] == "gpt-4.1-mini" and meta["fallbacks_used"] == 1


def test_other_questions_keep_the_usual_reference(client):
    c, provider = client
    meta = ask(c, "Is my card blocked?").json()["router"]
    assert meta["reference_model"] == "gpt-5-medium" and "web_search_options" not in provider.calls[0]


def test_compare_on_a_live_question_uses_the_search_model(client):
    c, provider = client
    meta = ask(c, "delhi temperature", compare_with_reference=True).json()["router"]
    assert meta["routed_to"] == "gpt-5-search" and meta["compare"] is True
    assert provider.calls[0]["model"] == "openai/gpt-5-search-api"


def test_models_endpoint_shows_search_fee_and_reference(client):
    c, _ = client
    rows = {o["id"]: o for o in c.get("/api/models").json()["options"]}
    assert rows["gpt-5-search"]["per_call_usd"] == 0.01 and rows["gpt-5-search"]["web_search"]
    assert rows["gpt-5-medium"]["reference"] and rows["gpt-5-search"]["reference"]
    assert "gpt-4o-mini-search" not in rows
    assert not rows["gpt-4.1-mini"]["reference"]
