"""Demo provider: scripted answers instead of real model calls (DEMO_MODE=true).

Everything else is real: routing, budget rules, retries, fallbacks, circuit breaker, cost
from token counts, logging. Use it to try the frontend for free. Add "#outage" to a prompt to
simulate the primary model failing, so you can watch a retry and a fallback happen.
"""
import hashlib
import time

import litellm

_ANSWERS = [
    (("which issue",),
     "Handle the **blocked card** first: the customer cannot pay for anything until it is unblocked. "
     "Then reverse the **duplicate charge**, then chase the **missing refund**, which is older but not blocking."),
    (("prepay",),
     "**51 months sooner.** EMI Rs 35,348; balance after 36 EMIs Rs 37,46,564; after the Rs 5 lakh "
     "prepayment Rs 32,46,564, repaid in 153 more EMIs (the last one partial), so the loan ends after "
     "189 months instead of 240."),
    (("charged twice", "double charge", "debited twice"),
     "Intent: card_payment_charged_twice. Route to the disputes queue; the duplicate debit is usually "
     "reversed within 5-7 working days."),
    (("emi",),
     "EMI = P x r x (1+r)^n / ((1+r)^n - 1). For Rs 10,00,000 at 10% a year for 5 years: monthly rate "
     "0.8333%, n = 60, so the EMI is about Rs 21,247."),
    (("extract", "sanction letter", "json"),
     '{"borrower_name": "Priya Sharma", "pan": "ABCPS1234K", "loan_amount": 2500000, '
     '"sanction_date": "2026-10-12", "interest_rate": 8.75}'),
    (("sql", "query"),
     "SELECT m.city, m.merchant_name, SUM(t.amount) AS spend\nFROM transactions t JOIN merchants m "
     "USING (merchant_id)\nGROUP BY m.city, m.merchant_name\nORDER BY spend DESC\nLIMIT 3;"),
    (("python", "luhn", "function"),
     "def luhn_valid(number: str) -> bool:\n    digits = [int(d) for d in number if d.isdigit()]\n"
     "    if len(digits) < 12:\n        return False\n    total = 0\n"
     "    for i, d in enumerate(reversed(digits)):\n        if i % 2 == 1:\n            d = d * 2 - 9 if d > 4 else d * 2\n"
     "        total += d\n    return total % 10 == 0"),
    (("weather", "temperature", "forecast", "news", "sensex", "nifty", "exchange rate", "share price"),
     "Demo mode: this question went to a web-search model. With real models on, the answer comes from a "
     "live web search, with links to its sources."),
    (("foir",),
     "FOIR = total monthly EMIs / monthly income = (22,000 + 15,000) / 90,000 = 41.1%. Below the usual "
     "50% ceiling, so the application passes this check."),
]
_DEFAULT = "Demo mode: this answer is scripted. Start the router without DEMO_MODE to call real models."
_THINKING = {None: 0, "minimal": 0, "low": 60, "medium": 160, "high": 320}


def _prompt_text(messages: list[dict]) -> str:
    return " ".join(str(m.get("content", "")) for m in messages)


def _answer_for(prompt: str) -> str:
    low = prompt.lower()
    for keys, answer in _ANSWERS:
        if any(k in low for k in keys):
            return answer
    return _DEFAULT


class DemoProvider:
    def __init__(self, sleep=time.sleep, primary_model: str = "openai/gpt-4.1-mini"):
        self._sleep = sleep
        self.primary_model = primary_model

    def __call__(self, **kwargs):
        prompt = _prompt_text(kwargs["messages"])
        model = kwargs["model"]
        effort = kwargs.get("reasoning_effort")
        seed = int(hashlib.sha256((prompt + model).encode()).hexdigest()[:6], 16)
        self._sleep(0.35 + (seed % 60) / 100 + _THINKING[effort] / 400)  # 0.35-2 s, longer when thinking

        if "#outage" in prompt.lower() and model == self.primary_model:
            raise litellm.RateLimitError("Demo: simulated rate limit", "openai", model)

        answer = _answer_for(prompt)
        return {
            "choices": [{"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(prompt) // 4 + 12,
                      "completion_tokens": len(answer) // 4 + 4 + _THINKING[effort]},
        }
