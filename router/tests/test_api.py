"""API behaviour: OpenAI-compatible shape, routing metadata, limits, overrides, budgets, errors."""
import json

import pytest
from fastapi.testclient import TestClient

from app.limits import RateLimiter
from app.main import build_service, create_app
from conftest import FakeClock, FakeProvider, Slow, err

MINI, MINI5, FULL = "openai/gpt-4.1-mini", "openai/gpt-5-mini", "openai/gpt-4.1"
ADMIN = {"Authorization": "Bearer admin-key"}


@pytest.fixture
def make_client(settings):
    def _make(script=None, rate_limiter=None, **overrides):
        s = settings.model_copy(update=overrides)
        clock = FakeClock()
        provider = FakeProvider(clock, script)
        service = build_service(s, provider, clock, clock.sleep)
        app = create_app(s, service, rate_limiter)
        return TestClient(app), provider, service
    return _make


def ask(client, content="Is my card blocked?", headers=None, **body):
    payload = {"model": "auto", "messages": [{"role": "user", "content": content}], **body}
    return client.post("/v1/chat/completions", json=payload, headers=headers or {})


def test_openai_compatible_response_with_routing_metadata(make_client):
    client, provider, _ = make_client({MINI: ["Your card is active."]})
    r = ask(client)
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion" and body["model"] == "gpt-4.1-mini"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Your card is active."}
    assert body["usage"] == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    meta = body["router"]
    assert meta["routed_to"] == "gpt-4.1-mini" and meta["policy_version"] == "static-0"
    assert meta["cost_basis"] == "calculated" and meta["price_checked"] == "2026-10-28"
    assert meta["fallbacks_used"] == 0 and meta["downgraded"] is False and meta["forced"] is False
    assert meta["cost_usd"] == pytest.approx((100 * 0.40 + 50 * 1.60) / 1e6)
    assert meta["reference_cost_usd"] == pytest.approx((100 * 1.25 + 50 * 10.0) / 1e6)
    assert meta["attempts"] == [{"model": "gpt-4.1-mini", "outcome": "ok", "error_category": None,
                                 "latency_ms": 0, "retry": False}]


def test_works_with_the_official_openai_sdk_shape(make_client):
    client, _, _ = make_client()
    r = ask(client, user="abc", metadata={"x": 1}, max_completion_tokens=50)  # extra SDK fields are ignored
    assert r.status_code == 200


def test_fallback_is_visible_in_metadata_and_logs(make_client):
    client, provider, service = make_client({MINI: [err("not_found")]})
    body = ask(client).json()
    assert body["router"]["routed_to"] == "gpt-5-mini-low" and body["router"]["fallbacks_used"] == 1
    row = service.tracker.get(body["id"])
    assert row["fallbacks_used"] == 1 and row["status"] == "ok"
    assert [a["outcome"] for a in json.loads(row["attempts_json"])] == ["error", "ok"]


def test_no_prompt_text_is_stored(make_client):
    client, _, service = make_client()
    body = ask(client, content="My PAN is ABCDE1234F").json()
    assert "ABCDE1234F" not in json.dumps(service.tracker.get(body["id"]))


def test_request_size_limit(make_client):
    client, provider, _ = make_client()
    r = ask(client, content="x" * 9000)
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large" and provider.calls == []


@pytest.mark.parametrize("payload, code", [
    ("not json", "invalid_request"),
    (json.dumps({"messages": []}), "invalid_request"),
    (json.dumps({"messages": [{"role": "robot", "content": "hi"}]}), "invalid_request"),
    (json.dumps({"messages": [{"role": "user", "content": "hi"}], "router": {"max_cost_usd": -1}}), "invalid_request"),
])
def test_invalid_requests(make_client, payload, code):
    client, _, _ = make_client()
    r = client.post("/v1/chat/completions", content=payload, headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == code


@pytest.mark.parametrize("extra, code", [({"stream": True}, "stream_not_supported"), ({"n": 2}, "n_not_supported")])
def test_unsupported_options(make_client, extra, code):
    client, _, _ = make_client()
    r = ask(client, **extra)
    assert r.status_code == 400 and r.json()["error"]["code"] == code


def test_max_tokens_is_capped(make_client):
    client, provider, _ = make_client()
    ask(client, max_tokens=50_000)
    assert provider.calls[0]["max_tokens"] == 1000


def test_forcing_a_model_needs_an_admin_key(make_client):
    client, provider, _ = make_client()
    r = ask(client, model="gpt-4.1")
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_override_forbidden"
    r = ask(client, model="gpt-4.1", headers={"Authorization": "Bearer client-key"})
    assert r.status_code == 403
    assert provider.calls == []


def test_admin_can_force_any_enabled_model_but_not_unknown_ones(make_client):
    client, provider, _ = make_client()
    r = ask(client, model="gpt-5-medium", headers=ADMIN)  # not demo-allowed, but admins may use it
    assert r.status_code == 200 and r.json()["router"]["forced"] is True
    assert provider.calls[0]["model"] == "openai/gpt-5" and provider.calls[0]["reasoning_effort"] == "medium"
    r = ask(client, model="o4-mini-medium", headers=ADMIN)  # disabled
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_model"


def test_public_callers_never_reach_options_outside_the_demo_allowlist(make_client):
    client, provider, _ = make_client(default_chain="gpt-5-medium,gpt-4.1-mini")
    body = ask(client).json()
    assert body["router"]["routed_to"] == "gpt-4.1-mini" and provider.models_called() == [MINI]


def test_api_key_required_when_configured(make_client):
    client, _, _ = make_client(require_api_key=True)
    assert ask(client).status_code == 401
    assert ask(client, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert ask(client, headers={"Authorization": "Bearer client-key"}).status_code == 200


def test_wrong_key_is_rejected_even_when_keys_are_optional(make_client):
    client, _, _ = make_client()
    r = ask(client, headers={"Authorization": "Bearer guessed"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_rate_limit(make_client):
    client, _, _ = make_client(rate_limiter=RateLimiter(per_minute=2, per_day=100))
    assert ask(client).status_code == 200
    assert ask(client).status_code == 200
    r = ask(client)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert ask(client, headers=ADMIN).status_code == 200  # admins are not rate limited


def test_caller_cannot_raise_the_cost_limit(make_client):
    # server limit is tiny; asking for a higher limit must not help
    client, provider, _ = make_client(max_cost_per_request_usd=0.000001)
    r = ask(client, router={"max_cost_usd": 10})
    assert r.status_code == 400 and r.json()["error"]["code"] == "budget_too_low" and provider.calls == []


def test_budget_too_low_names_the_cheapest_qualifying_model(make_client):
    client, _, _ = make_client()
    err_body = ask(client, router={"max_cost_usd": 0.0000001}).json()["error"]
    assert err_body["code"] == "budget_too_low"
    assert err_body["cheapest_qualifying_model"] == "gpt-4.1-mini"


def test_downgrade_stays_inside_the_qualifying_chain(make_client):
    client, provider, _ = make_client(default_chain="gpt-4.1,gpt-4.1-mini")
    # gpt-4.1 worst case at 800 tokens is ~$0.0064; gpt-4.1-mini is ~$0.0013
    body = ask(client, router={"max_cost_usd": 0.002}).json()
    assert body["router"]["routed_to"] == "gpt-4.1-mini" and body["router"]["downgraded"] is True
    assert "downgraded from 'gpt-4.1'" in body["router"]["reason"]
    assert provider.models_called() == [MINI]


def test_daily_budget(make_client):
    client, provider, service = make_client(daily_budget_usd=0.0001)
    r = ask(client)
    assert r.status_code == 429 and r.json()["error"]["code"] == "daily_budget_reached" and provider.calls == []


def test_all_models_failing_returns_503_and_is_logged(make_client):
    script = {MINI: [err("server")] * 2, MINI5: [err("server")] * 2, FULL: [err("server")] * 2}
    client, _, service = make_client(script, max_model_calls=10)
    r = ask(client)
    assert r.status_code == 503
    e = r.json()["error"]
    assert e["code"] == "all_models_failed" and len(e["attempts"]) == 6
    row = service.tracker.get(e["request_id"])
    assert row["status"] == "all_models_failed" and row["routed_to"] is None


def test_provider_auth_failure_is_502_without_leaking_details(make_client):
    client, _, _ = make_client({MINI: [err("auth")]})
    r = ask(client)
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"
    assert "bad key" not in r.text


def test_latency_limit_from_caller(make_client):
    client, provider, _ = make_client({MINI: [Slow(5, "late"), "unused"]})
    r = ask(client, router={"max_latency_ms": 3000})
    assert provider.calls[0]["timeout"] == pytest.approx(3.0)
    assert r.status_code == 200  # the fake returned; a real provider would have timed out at 3 s


def test_health_models_and_stats(make_client):
    client, _, _ = make_client()
    ask(client)
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and "gpt-4.1-mini" in health["models_enabled"]
    models = client.get("/api/models").json()["options"]
    assert {"id", "tier", "input_usd_per_mtok", "demo_allowed"} <= set(models[0])
    assert client.get("/v1/models").json()["data"][0]["id"] == "auto"
    stats = client.get("/api/stats").json()
    assert stats["requests"] == 1 and stats["requests_by_model"] == {"gpt-4.1-mini": 1}
