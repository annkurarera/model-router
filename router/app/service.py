"""The request path: decide -> budget rules -> bounded execution -> log -> response.

Kept free of FastAPI so it can be tested and reused by the evaluation harness.
"""
import time
import uuid
from dataclasses import dataclass

from .config import Settings
from .executor import ChainResult, Executor, StopReason
from .policy import Decision, RulesPolicy, StaticPolicy
from .registry import Registry, RegistryError, estimate_prompt_tokens
from .schemas import ChatRequest, RouterError
from .tracker import Tracker, hash_client

# chain stop reason -> (HTTP status, error code, message)
_FAILURES = {
    StopReason.EXHAUSTED: (503, "all_models_failed", "Every model in the fallback chain failed."),
    StopReason.DEADLINE: (504, "deadline_exceeded", "The request deadline passed before a model answered."),
    StopReason.BUDGET: (402, "budget_exhausted", "The request's cost limit was reached before a valid answer."),
    StopReason.CALL_LIMIT: (503, "call_limit_reached", "The per-request model call limit was reached."),
    StopReason.NON_RETRYABLE: (502, "upstream_error", "The model provider rejected the request."),
}


@dataclass
class BudgetPlan:
    chain: list[str]
    downgraded: bool
    estimated_cost_usd: float
    note: str = ""


def plan_budget(registry: Registry, chain: list[str], prompt_tokens: int, max_tokens: int,
                max_cost_usd: float) -> BudgetPlan:
    """Budget rules (plan: Routing logic). Only options already in the chain (the qualifying set)
    are considered, so a downgrade never drops below the quality bar."""
    worst = {oid: registry.get(oid).worst_case_cost_usd(prompt_tokens, max_tokens) for oid in chain}
    primary = chain[0]
    if worst[primary] <= max_cost_usd:
        return BudgetPlan(chain, False, worst[primary])
    fitting = sorted((oid for oid in chain if worst[oid] <= max_cost_usd), key=lambda o: worst[o])
    if not fitting:
        cheapest = min(chain, key=lambda o: worst[o])
        raise RouterError(
            400, "budget_too_low",
            f"No qualifying model fits max_cost_usd={max_cost_usd}. Cheapest qualifying option is "
            f"'{cheapest}' at an estimated ${worst[cheapest]:.6f}. Raise the limit or use mode 'cheapest'.",
            cheapest_qualifying_model=cheapest, cheapest_estimated_cost_usd=round(worst[cheapest], 6))
    return BudgetPlan(fitting, True, worst[fitting[0]],
                      f"downgraded from '{primary}' (est. ${worst[primary]:.6f}) to fit max_cost_usd")


class RouterService:
    def __init__(self, settings: Settings, registry: Registry, executor: Executor, tracker: Tracker,
                 policy: StaticPolicy | RulesPolicy, clock=time.monotonic):
        self.settings = settings
        self.registry = registry
        self.executor = executor
        self.tracker = tracker
        self.policy = policy
        self._clock = clock

    def _reference_for(self, decision: Decision) -> str:
        """The strongest model for this kind of question. Live-data questions need web search,
        so they are compared with the strongest search model, not a model without internet."""
        if decision.category == "live_data":
            return self.settings.reference_search_model
        return self.settings.reference_model

    def _decide(self, req: ChatRequest, messages: list[dict], role: str) -> Decision:
        if req.router.compare_with_reference:
            d = self.policy.decide(messages, req.router.mode)  # category and difficulty, for the record
            reference = self._reference_for(d)
            if not self.registry.has(reference) or not self.registry.get(reference).enabled:
                raise RouterError(503, "compare_unavailable", "The reference model is not enabled.")
            d.chain = [reference]
            d.compare = True
            d.reason = f"compare: the same question on the reference model {reference}"
            return d
        if req.model in ("auto", "router"):
            return self.policy.decide(messages, req.router.mode)
        if role != "admin":
            raise RouterError(403, "model_override_forbidden",
                              "Choosing a specific model needs an admin key. Use model 'auto'.")
        try:
            self.registry.validate_chain([req.model])
        except RegistryError:
            raise RouterError(400, "unknown_model", f"'{req.model}' is not an enabled model option.") from None
        return Decision([req.model], "forced by admin request", self.policy.version, req.router.mode, forced=True)

    def handle(self, req: ChatRequest, role: str, identity: str) -> dict:
        s = self.settings
        if req.stream:
            raise RouterError(400, "stream_not_supported", "Streaming is not supported yet.")
        if req.n != 1:
            raise RouterError(400, "n_not_supported", "Only n=1 is supported.")

        started = self._clock()
        request_id = f"rtr-{uuid.uuid4().hex[:20]}"
        messages = [m.model_dump() for m in req.messages]
        max_tokens = min(req.requested_max_tokens or s.default_max_tokens, s.max_tokens_cap)
        max_cost = min(req.router.max_cost_usd or s.max_cost_per_request_usd, s.max_cost_per_request_usd)
        deadline_s = s.request_deadline_s
        if req.router.max_latency_ms:
            deadline_s = min(deadline_s, req.router.max_latency_ms / 1000)

        decision = self._decide(req, messages, role)
        if role != "admin" and not decision.compare:  # the public demo never reaches expensive options
            allowed = [o for o in decision.chain if self.registry.get(o).demo_allowed]
            if not allowed:
                raise RouterError(403, "model_not_allowed", "No model in the chain is allowed for this caller.")
            decision.chain = allowed

        prompt_tokens = estimate_prompt_tokens(messages)
        plan = plan_budget(self.registry, decision.chain, prompt_tokens, max_tokens, max_cost)

        spent_today = self.tracker.spent_today()
        if spent_today + plan.estimated_cost_usd > s.daily_budget_usd:
            raise RouterError(429, "daily_budget_reached",
                              "The router's daily budget is used up. Try again tomorrow.")

        chain_result = self.executor.run_chain(
            plan.chain, messages, max_tokens, req.temperature, req.response_format,
            max_cost_usd=max_cost, deadline_s=deadline_s)
        latency_ms = int((self._clock() - started) * 1000)

        record = self._record(request_id, identity, decision, plan, chain_result, latency_ms)
        prompt_text = "\n".join(str(m["content"]) for m in messages)

        if not chain_result.ok:
            status, code, message = _FAILURES[chain_result.stop_reason]
            record["status"] = code
            self.tracker.log(record, prompt_text)
            raise RouterError(status, code, message, request_id=request_id,
                              attempts=[a.public() for a in chain_result.attempts])

        self.tracker.log(record, prompt_text, chain_result.result.text)
        return self._response(request_id, decision, plan, chain_result, record, latency_ms)

    def _record(self, request_id, identity, decision: Decision, plan: BudgetPlan, cr: ChainResult,
                latency_ms: int) -> dict:
        result = cr.result
        reference = self._reference_for(decision)
        reference_cost = None
        if result is not None and self.registry.has(reference):
            reference_cost = self.registry.get(reference).cost_usd(result.prompt_tokens, result.completion_tokens)
        reason = decision.reason + (f"; {plan.note}" if plan.note else "")
        return {
            "id": request_id,
            "client_hash": hash_client(identity),
            "policy_version": decision.policy_version,
            "mode": decision.mode,
            "category": decision.category,
            "difficulty": decision.difficulty,
            "confidence": decision.confidence,
            "routed_to": result.option.id if result else None,
            "reasoning_effort": result.option.reasoning_effort if result else None,
            "reason": reason,
            "forced": int(decision.forced),
            "compare": int(decision.compare),
            "downgraded": int(plan.downgraded),
            "fallbacks_used": cr.fallbacks_used,
            "calls_made": cr.calls_made,
            "attempts_json": [a.public() for a in cr.attempts],
            "status": "ok" if cr.ok else cr.stop_reason.value,
            "error_category": cr.error_category.value if cr.error_category else None,
            "schema_valid": None,  # validator arrives in session 7
            "prompt_tokens": sum(a.prompt_tokens for a in cr.attempts),
            "completion_tokens": sum(a.completion_tokens for a in cr.attempts),
            "estimated_cost_usd": round(plan.estimated_cost_usd, 8),
            "cost_usd": round(cr.total_cost_usd, 8),
            "reference_cost_usd": round(reference_cost, 8) if reference_cost is not None else None,
            "reference_model": reference,
            "latency_ms": latency_ms,
        }

    def _response(self, request_id, decision: Decision, plan: BudgetPlan, cr: ChainResult, record: dict,
                  latency_ms: int) -> dict:
        r = cr.result
        return {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": r.option.id,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": r.text},
                         "finish_reason": r.finish_reason or "stop"}],
            "usage": {"prompt_tokens": record["prompt_tokens"], "completion_tokens": record["completion_tokens"],
                      "total_tokens": record["prompt_tokens"] + record["completion_tokens"]},
            "router": {
                "routed_to": r.option.id,
                "provider_model": r.option.model,
                "reasoning_effort": r.option.reasoning_effort,
                "category": decision.category,
                "difficulty": decision.difficulty,
                "confidence": decision.confidence,
                "reason": record["reason"],
                "policy_version": decision.policy_version,
                "mode": decision.mode,
                "forced": decision.forced,
                "compare": decision.compare,
                "signals": decision.signals,
                "downgraded": plan.downgraded,
                "fallbacks_used": cr.fallbacks_used,
                "attempts": record["attempts_json"],
                "schema_valid": None,
                "estimated_cost_usd": record["estimated_cost_usd"],
                "cost_usd": record["cost_usd"],
                "router_overhead_usd": 0.0,  # no classifier call yet
                # simulated = demo mode (scripted token counts); calculated = real token usage x price list.
                # Neither is the provider's bill, which can differ slightly (caching, rounding).
                "cost_basis": "simulated" if self.settings.demo_mode else "calculated",
                "price_checked": self.registry.price_checked,
                "reference_model": record["reference_model"],
                "reference_cost_usd": record["reference_cost_usd"],
                "latency_ms": latency_ms,
            },
        }
