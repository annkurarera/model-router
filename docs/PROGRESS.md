# Progress log

## Session 1: serving engine (done)

Built
- Model pool (`models.yaml`) with prices, tiers and reasoning-effort options; validated on load
- Executor with per-call timeout, request deadline, 1 retry for transient errors, 2 fallback hops,
  4-call limit, per-request budget check before every call, circuit breaker (5 failures, 60 s)
- Error categories mapped from LiteLLM exceptions; auth and bad-request errors stop the chain
- Billed-but-empty responses (reasoning models out of output budget) count toward cost and fall back
- Budget rules: downgrade only within the qualifying chain, `budget_too_low` with the cheapest option,
  daily budget
- OpenAI-compatible API, API keys with client and admin roles, rate limits, request-size limit,
  `max_tokens` cap, demo allowlist
- Metadata-only request log; local debug mode stores redacted text for 7 days
- 95 tests: fake providers for every failure scenario plus real LiteLLM in offline mock mode

Decisions
- Costs are computed from token usage and `models.yaml`, not LiteLLM's built-in price map, so every
  number traces back to one reviewed file
- Token estimates for budget checks use ~3 characters per token (no tokenizer download); this
  over-estimates, which is the safe side
- A slow call that still returns inside its timeout is kept; the deadline is enforced before each call
- `StaticPolicy` fills the policy slot until calibration exists; the `decide()` interface stays the same

Open items for later sessions
- `schema_valid` is always null until the validator (session 7)
- `router_overhead_usd` is 0 until the classifier exists (session 5)
- Rate-limit counters are in memory and reset on restart; the daily USD budget is stored in the log

## Early playground (between sessions 1 and 2)

Built
- Next.js playground: route trace (each attempt, retries, fallbacks), answer, cost receipt against the
  reference model, session history, Models and Usage tabs; works on mobile
- Demo mode in the router (`DEMO_MODE=true`): scripted answers, everything else real; `#outage` simulates
  a primary-model failure
- 28 Playwright tests (14 on desktop and mobile) with a mocked API; 3 backend tests for demo mode

Decisions
- Built ahead of plan at the user's request; it stays thin (no leaderboard yet) so evaluation work keeps priority
- CORS is limited to `CORS_ORIGINS` (default http://localhost:3000)

## Rule-based router and clearer front screen (between sessions 1 and 2)

Built
- `rules.py`: category (complaint, loan maths, extraction, SQL, Python, general), difficulty from signals
  (multi-step wording, count of numbers, fields to extract, separate complaint issues, length), JSON request flag
- `rules_policy.yaml` (`rules-1`): category x difficulty → chain; JSON never starts on the tiny tier; modes shift one level
- Compare: `router.compare_with_reference` runs only the reference model; limits apply; kept out of routing stats
- Usage: average cost per answer, answered requests by category, compare runs and spend
- Front screen: what the site does and how to try it, easy/hard example pairs (one click asks), Markdown answers,
  short-answer instruction, compare metrics table; fixed sideways scrolling on phones
- Log database migrates itself (adds the `compare` column to older databases)
- Tests: 133 backend, 42 frontend (21 on desktop and mobile)

Decisions
- No quality score in the live Compare: free-form questions have no correct answer to grade against; scored
  comparisons come with benchmark mode (session 10)
- gpt-5-mini-high allowed in the public demo (cheap per token; budgets cap its thinking cost); gpt-5 stays admin-only
  except through Compare
- The playground examples go into the leakage check in session 2, so none of them can end up in an eval set
- The rules were written before any benchmark data and will not be tuned on the test set

## Loan-maths calculator and honest cost labels (start of session 2)

Found
- The demo-mode answer for the prepayment example said "34 months sooner". It was hand-written, not computed,
  and wrong: the exact figure is 51 months (balance after 36 EMIs Rs 37,46,564; 153 more EMIs; ends month 189).
  Corrected, and a test now checks the demo text against the calculator

Built
- `data_gen/finance.py`: EMI (rounded up to the paisa), schedule, balance after N payments, months to repay,
  prepayment keeping EMI or tenure, total interest, FOIR, LTV, processing fee with GST; conventions documented
- 66 tests: reference values, invariants over 36 loans (ends exactly on time, repays the principal, balance falls),
  the worked example, monotonic prepayment savings, every invalid input
- `cost_basis` (calculated or simulated) and `price_checked` on every response and on /api/stats; shown on the
  receipt, in Compare and on the Usage tab; savings wording says it is not a benchmark result

Decisions
- EMI rounds up to the paisa and balances stay unrounded, so rounding can never add an extra month (the invariant
  tests caught a 241st month on a Rs 75 lakh, 18%, 20-year loan under the earlier rounding)
- Loan-maths answers in the benchmark are graded against this calculator, never against another model

## Live fix: slow reasoning models (first real use)

Found
- A hard SQL question went to gpt-5-mini-high (high reasoning effort). It needed more than the single 20 s
  per-call limit, timed out, was retried, timed out again, then gpt-4.1 answered: 43 s in total

Changed
- Time limit per model in `models.yaml` (`timeout_s`): 20 s for quick models, 30 s mid, 75 s for reasoning models
- A timeout is no longer retried on the same model; the router falls back at once. Retries stay for rate
  limits, server errors and dropped connections
- Request deadline 45 s → 110 s (one slow reasoning call plus a quick fallback); the browser waits 120 s
- Tests: 139 backend

## Live web questions (after first real use)

Found
- A visitor asked for Delhi's temperature. No model in the pool can see live data, so the answer was "I don't have
  real-time data"

Built
- `live_data` category: weather, news, market prices, exchange rates, "latest", "today", "right now" and similar.
  Checked only when no banking or code category matched, so "charged twice today" stays a complaint
- Web-search option `gpt-5-search` (gpt-5-search-api): $1.25 / $10 per million tokens plus $0.01 per search
  (`per_call_usd` in `models.yaml`, included in every cost and budget check). gpt-4.1-mini is the fallback, so a
  search outage still gets a reply
- Live questions are measured against the search model (`REFERENCE_SEARCH_MODEL`), because gpt-5-medium cannot
  search and would be a meaningless yardstick
- Models tab: shows the search fee; gpt-5-medium is labelled "Compare only" instead of "Admin only"; answer links
  open in a new tab
- Tests: 164 backend, 44 frontend

Found on first run
- The gpt-4o search-preview models were retired by OpenAI ("has been deprecated"); they stay in `models.yaml`
  disabled, and gpt-5-search-api replaced them

Decisions
- Web search is only for live questions; no other category may route to a search model, so banking traffic never
  pays the search fee
- A live search costs about 1-2 US cents, far more than an easy routed answer; the $2 daily budget allows
  roughly 100 searches a day
