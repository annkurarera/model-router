"""Rule-based routing: categories, difficulty signals, JSON floor, modes, policy file."""
import pytest
from fastapi.testclient import TestClient

from app.main import build_service, create_app
from app.policy import RulesPolicy
from app.registry import Registry, RegistryError
from app.rules import analyse, shift
from conftest import MODELS_FILE, FakeClock, FakeProvider

RULES = MODELS_FILE.parent / "rules_policy.yaml"


def a(text):
    return analyse([{"role": "system", "content": "Answer briefly, use Markdown for code."},
                    {"role": "user", "content": text}])


# The playground's example pairs: each must land on the intended category and difficulty.
EXAMPLES = [
    ("Tag this complaint with one intent: my card was charged twice for one purchase.", "complaint", "easy"),
    ("A customer writes: my card was charged twice, the card is now blocked and last month's refund never "
     "arrived. Which issue should the bank handle first, and why?", "complaint", "hard"),
    ("What is the EMI on a Rs 10 lakh loan at 10% a year for 5 years?", "loan_maths", "easy"),
    ("Home loan of Rs 40 lakh at 8.75% for 20 years. After 3 years I prepay Rs 5 lakh and keep the same EMI. "
     "How many months sooner does the loan end?", "loan_maths", "hard"),
    ("Extract the borrower name and loan amount: Dear Priya Sharma, we sanction a home loan of Rs 25,00,000.",
     "extraction", "easy"),
    ("Extract borrower name, PAN, co-applicant, loan amount, rate, tenure, sanction date and processing fee as "
     "JSON from: Ref HL/2026/118. Sanctioned 12-Oct-26 to Ms Priya Sharma (PAN ABCPS1234K), co-applicant Rahul "
     "Sharma: Rs 25 lakh at 8.75% p.a. for 240 months; fee 0.5% + GST.", "extraction", "hard"),
    ("Write SQL for total card spend by merchant category.", "sql", "easy"),
    ("Write SQL for the top 3 merchants in each city by card spend, with each merchant's share of its city's "
     "total. Handle ties.", "sql", "hard"),
    ("Write a Python function that checks a card number with the Luhn algorithm.", "python", "easy"),
    ("Is my card blocked?", "general", "easy"),
]


@pytest.mark.parametrize("text, category, difficulty", EXAMPLES)
def test_playground_examples_are_classified_as_intended(text, category, difficulty):
    r = a(text)
    assert (r.category, r.difficulty) == (category, difficulty), r.signals


def test_only_the_last_user_message_is_analysed():
    msgs = [{"role": "user", "content": "Write SQL for the top 3 per city, handle ties, each share"},
            {"role": "assistant", "content": "SELECT ..."},
            {"role": "user", "content": "Thanks! Is my card blocked?"}]
    assert analyse(msgs).category == "general"


def test_code_beats_money_words():
    assert a("Write a Python function for EMI at 10% interest").category == "python"
    assert a("SELECT customer_id FROM loans WHERE interest > 10").category == "sql"


def test_signals_explain_the_difficulty():
    r = a(EXAMPLES[3][0])
    assert r.score >= 3
    assert any("numbers" in s for s in r.signals) and any(s.startswith("multi-step") for s in r.signals)


def test_complaint_with_several_issues_is_harder():
    one = a("Classify this complaint: my refund never arrived.")
    many = a("Classify this complaint: my refund never arrived and there is a fraud transaction and a fee.")
    assert many.score > one.score and any("issues" in s for s in many.signals)


def test_json_request_is_detected():
    assert a("Extract the name as JSON from: Dear Priya").structured_output
    assert not a("Extract the name from: Dear Priya").structured_output


@pytest.mark.parametrize("level, mode, expected", [
    ("easy", "cheapest", "easy"), ("medium", "cheapest", "easy"), ("hard", "quality", "hard"),
    ("easy", "quality", "medium"), ("medium", "balanced", "medium"),
])
def test_modes_shift_difficulty_one_level(level, mode, expected):
    assert shift(level, mode) == expected


@pytest.fixture
def policy(registry):
    return RulesPolicy(registry, RULES)


def test_policy_file_covers_every_category_and_difficulty(policy):
    assert policy.version == "rules-1"
    for chains in policy.chains.values():
        assert set(chains) == {"easy", "medium", "hard"}


def test_easy_and_hard_route_to_different_models(policy):
    easy = policy.decide([{"role": "user", "content": EXAMPLES[2][0]}])
    hard = policy.decide([{"role": "user", "content": EXAMPLES[3][0]}])
    assert easy.chain[0] == "gpt-5-mini-low" and hard.chain[0] == "gpt-5-mini-medium"
    assert hard.category == "loan_maths" and hard.difficulty == "hard"
    assert hard.reason.startswith("rules: loan maths, hard (") and "→ gpt-5-mini-medium" in hard.reason


def test_quality_mode_lets_hard_questions_think_longest(policy):
    """Live finding: high effort took ~40 s on hard SQL, so balanced uses medium and quality keeps high."""
    for text in (EXAMPLES[3][0], EXAMPLES[7][0]):  # hard loan maths, hard SQL
        balanced = policy.decide([{"role": "user", "content": text}])
        quality = policy.decide([{"role": "user", "content": text}], mode="quality")
        assert balanced.chain[0] == "gpt-5-mini-medium" and quality.chain[0] == "gpt-5-mini-high"
        assert "quality mode: longest thinking" in quality.reason


def test_medium_effort_costs_the_same_per_token_as_high(registry):
    medium, high = registry.get("gpt-5-mini-medium"), registry.get("gpt-5-mini-high")
    assert medium.cost_usd(1000, 1000) == high.cost_usd(1000, 1000)
    assert medium.timeout_s < high.timeout_s and medium.min_output_tokens < high.min_output_tokens


def test_cheapest_mode_moves_down_and_says_so(policy):
    d = policy.decide([{"role": "user", "content": EXAMPLES[3][0]}], mode="cheapest")
    assert d.chain[0] == policy.chains["loan_maths"]["medium"][0]
    assert "cheapest mode: routed as medium" in d.reason and d.difficulty == "hard"


def test_json_request_never_starts_on_a_tiny_model(policy):
    d = policy.decide([{"role": "user", "content": "Is my card blocked? Reply in JSON with a yes/no field."}])
    assert policy.registry.get(d.chain[0]).tier != "tiny"
    assert "JSON requested" in d.reason


def test_disabled_options_are_skipped(policy):
    # gpt-4.1-nano is disabled in models.yaml; no chain may contain it
    assert all("gpt-4.1-nano" not in chain for c in policy.chains.values() for chain in c.values())


def test_policy_with_no_usable_option_fails_loudly(tmp_path, registry):
    bad = tmp_path / "p.yaml"
    text = RULES.read_text().replace("easy:   [gpt-5-nano-minimal, gpt-4.1-mini, gpt-4.1]",
                                     "easy:   [o4-mini-medium]", 1)
    bad.write_text(text)
    with pytest.raises(RegistryError, match="complaint/easy"):
        RulesPolicy(registry, bad)


# ---- through the API -------------------------------------------------------------------------------
@pytest.fixture
def rules_client(settings):
    s = settings.model_copy(update={"routing_policy": "rules"})
    clock = FakeClock()
    provider = FakeProvider(clock)
    service = build_service(s, provider, clock, clock.sleep)
    return TestClient(create_app(s, service)), provider, service


def ask(client, text, **router):
    return client.post("/v1/chat/completions",
                       json={"model": "auto", "messages": [{"role": "user", "content": text}], "router": router})


def test_api_reports_category_difficulty_and_signals(rules_client):
    client, provider, _ = rules_client
    body = ask(client, EXAMPLES[3][0]).json()
    meta = body["router"]
    assert meta["policy_version"] == "rules-1" and meta["routed_to"] == "gpt-5-mini-medium"
    assert (meta["category"], meta["difficulty"]) == ("loan_maths", "hard")
    assert meta["signals"] and provider.calls[0]["reasoning_effort"] == "medium"
    assert client.get("/api/health").json()["policy_version"] == "rules-1"


def test_compare_runs_only_the_reference_model_for_public_callers(rules_client):
    client, provider, service = rules_client
    body = ask(client, EXAMPLES[2][0], compare_with_reference=True).json()
    meta = body["router"]
    assert meta["routed_to"] == "gpt-5-medium" and meta["compare"] is True  # not demo-allowed, but compare may
    assert meta["category"] == "loan_maths" and meta["reason"].startswith("compare:")
    assert provider.calls[0]["model"] == "openai/gpt-5"
    stats = client.get("/api/stats").json()
    assert stats["compare_runs"] == 1 and stats["requests"] == 0  # kept out of routing figures


def test_compare_still_respects_cost_limits(rules_client):
    client, provider, _ = rules_client
    r = ask(client, "hello", compare_with_reference=True, max_cost_usd=0.000001)
    assert r.status_code == 400 and r.json()["error"]["code"] == "budget_too_low" and provider.calls == []


def test_compare_counts_against_the_rate_limit(settings):
    from app.limits import RateLimiter
    s = settings.model_copy(update={"routing_policy": "rules"})
    clock = FakeClock()
    service = build_service(s, FakeProvider(clock), clock, clock.sleep)
    client = TestClient(create_app(s, service, RateLimiter(per_minute=1, per_day=100)))
    assert ask(client, "hi").status_code == 200
    assert ask(client, "hi", compare_with_reference=True).status_code == 429


def test_usage_reports_average_cost_and_categories(rules_client):
    client, _, _ = rules_client
    ask(client, EXAMPLES[0][0])
    ask(client, EXAMPLES[2][0])
    stats = client.get("/api/stats").json()
    assert stats["requests_by_category"] == {"complaint": 1, "loan_maths": 1}
    assert stats["avg_cost_per_answer_usd"] > 0


def test_old_log_database_gains_the_new_column(tmp_path):
    import sqlite3
    from app.tracker import Tracker
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, day TEXT NOT NULL, "
                 "status TEXT NOT NULL, cost_usd REAL, reference_cost_usd REAL, routed_to TEXT, fallbacks_used INTEGER,"
                 " latency_ms INTEGER, error_category TEXT, category TEXT, client_hash TEXT, policy_version TEXT, "
                 "mode TEXT, difficulty TEXT, confidence REAL, reasoning_effort TEXT, reason TEXT, forced INTEGER, "
                 "downgraded INTEGER, calls_made INTEGER, attempts_json TEXT, schema_valid INTEGER, "
                 "prompt_tokens INTEGER, completion_tokens INTEGER, estimated_cost_usd REAL)")
    conn.commit()
    conn.close()
    t = Tracker(str(path))
    t.log({"id": "r1", "status": "ok", "compare": 1, "cost_usd": 0.01})
    assert t.stats()["compare_runs"] == 1


def test_registry_fixture_is_the_real_models_file(registry):
    assert isinstance(registry, Registry)
