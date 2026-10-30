import pytest

from app.registry import ModelOption, Registry, RegistryError, estimate_prompt_tokens
from conftest import MODELS_FILE


def test_real_models_file_loads_and_validates(registry):
    ids = [o.id for o in registry.enabled()]
    assert "gpt-4.1-mini" in ids and "gpt-5-medium" in ids
    assert "o4-mini-medium" not in ids  # disabled in models.yaml
    assert registry.price_checked == "2026-10-28"


def test_every_default_chain_option_is_enabled_and_demo_allowed(registry, settings):
    registry.validate_chain(settings.default_chain_ids)
    assert all(registry.get(o).demo_allowed for o in settings.default_chain_ids)


def test_cost_is_tokens_times_price(registry):
    mini = registry.get("gpt-4.1-mini")  # $0.40 in, $1.60 out per 1M tokens
    assert mini.cost_usd(1_000_000, 0) == pytest.approx(0.40)
    assert mini.cost_usd(1000, 500) == pytest.approx(0.0004 + 0.0008)


def test_reasoning_options_reserve_thinking_tokens(registry):
    high = registry.get("gpt-5-mini-high")
    assert high.output_budget(800) == 8000
    assert registry.get("gpt-4.1-mini").output_budget(800) == 800
    assert high.worst_case_cost_usd(100, 800) == pytest.approx(high.cost_usd(100, 8000))


def _write(tmp_path, body: str):
    path = tmp_path / "models.yaml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = "  - {id: a, model: openai/a, tier: tiny, input_usd_per_mtok: 1, output_usd_per_mtok: 2"


@pytest.mark.parametrize("extra, message", [
    (", tier: huge}", "tier"),
    (", reasoning_effort: extreme}", "reasoning_effort"),
    (", input_usd_per_mtok: -1}", "negative"),
    (", colour: blue}", "unknown fields"),
])
def test_invalid_entries_are_rejected(tmp_path, extra, message):
    with pytest.raises(RegistryError, match=message):
        Registry.load(_write(tmp_path, "options:\n" + BASE + extra + "\n"))


def test_duplicate_ids_rejected(tmp_path):
    with pytest.raises(RegistryError, match="Duplicate"):
        Registry.load(_write(tmp_path, f"options:\n{BASE}}}\n{BASE}}}\n"))


def test_missing_price_rejected(tmp_path):
    with pytest.raises(RegistryError, match="output_usd_per_mtok"):
        Registry.load(_write(tmp_path, "options:\n  - {id: a, model: m, tier: tiny, input_usd_per_mtok: 1}\n"))


def test_chain_with_disabled_option_rejected(registry):
    with pytest.raises(RegistryError):
        registry.validate_chain(["gpt-4.1-mini", "o4-mini-medium"])


def test_prompt_token_estimate_is_conservative_and_handles_content_parts():
    plain = estimate_prompt_tokens([{"role": "user", "content": "x" * 300}])
    parts = estimate_prompt_tokens([{"role": "user", "content": [{"type": "text", "text": "x" * 300}]}])
    assert plain == parts >= 100  # 300 chars is ~75 real tokens; the estimate errs high


def test_model_option_is_immutable():
    option = ModelOption("a", "m", "tiny", 1, 1)
    with pytest.raises(Exception):
        option.tier = "mid"  # type: ignore[misc]


def test_models_file_path_constant():
    assert MODELS_FILE.exists()


def test_reasoning_options_have_longer_time_limits(registry):
    assert registry.get("gpt-5-mini-high").timeout_s > registry.get("gpt-4.1-mini").timeout_s


def test_non_positive_timeout_rejected(tmp_path):
    with pytest.raises(RegistryError, match="timeout_s"):
        Registry.load(_write(tmp_path, "options:\n" + BASE + ", timeout_s: 0}\n"))


def test_reasoning_options_leave_room_to_answer_after_thinking(registry):
    """Live finding: gpt-5-mini-high spent its whole 4,000-token budget thinking on a hard SQL question,
    returned an empty answer after 48 s, and the request fell back. Only tokens used are billed."""
    assert registry.get("gpt-5-mini-high").min_output_tokens >= 8000
    assert registry.get("gpt-5-mini-medium").min_output_tokens >= 4000
    worst = registry.get("gpt-5-mini-high").worst_case_cost_usd(200, 800)
    assert worst < 0.05  # still inside the default per-request cost limit
