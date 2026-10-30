"""One tiny real call per enabled model option: checks your API key, model access,
latency and cost accounting. Usually under one US cent in total (worst case about 5 cents,
if the reasoning models use their whole thinking budget).

Run from the router folder with the venv active:
    python scripts/check_models.py          # shows the plan and asks to confirm
    python scripts/check_models.py --yes    # no prompt
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.breaker import CircuitBreaker  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.executor import ExecutionLimits, Executor  # noqa: E402
from app.registry import Registry, estimate_prompt_tokens  # noqa: E402

MESSAGES = [{"role": "user", "content": "Reply with exactly one word: OK"}]
MAX_TOKENS = 16


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    settings = get_settings()
    registry = Registry.load(settings.models_file)
    options = registry.enabled()
    prompt_tokens = estimate_prompt_tokens(MESSAGES)
    worst = sum(o.worst_case_cost_usd(prompt_tokens, MAX_TOKENS) for o in options)
    print(f"{len(options)} enabled options. Worst-case total cost: ${worst:.4f} (usually far less).")
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        return 1

    executor = Executor(registry, CircuitBreaker(), ExecutionLimits(max_retries_per_call=0, max_fallback_hops=0))
    failures, total = 0, 0.0
    print(f"\n{'option':22} {'status':18} {'latency':>9} {'in':>5} {'out':>6} {'cost USD':>10}  answer")
    for o in options:
        r = executor.run_chain([o.id], MESSAGES, MAX_TOKENS)
        cost = r.total_cost_usd
        total += cost
        if r.ok:
            res = r.result
            print(f"{o.id:22} {'ok':18} {res.latency_ms:>7}ms {res.prompt_tokens:>5} {res.completion_tokens:>6} "
                  f"{cost:>10.6f}  {res.text.strip()[:20]!r}")
        else:
            failures += 1
            category = r.error_category.value if r.error_category else r.stop_reason.value
            print(f"{o.id:22} {'FAILED: ' + category:18} {'':>9} {'':>5} {'':>6} {cost:>10.6f}")
    print(f"\nTotal cost: ${total:.6f}. {len(options) - failures}/{len(options)} options working.")
    if failures:
        print("auth = check OPENAI_API_KEY in router/.env; not_found = your account cannot use that model "
              "(set enabled: false in app/models.yaml); invalid_response = raise min_output_tokens.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
