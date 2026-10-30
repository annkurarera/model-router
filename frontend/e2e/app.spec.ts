import { expect, test, type Page, type Route } from "@playwright/test";

const API = "http://localhost:8000";

const health = { status: "ok", demo_mode: false, policy_version: "rules-1",
  models_enabled: ["gpt-5-nano-minimal", "gpt-4.1-mini", "gpt-5-mini-low", "gpt-4.1", "gpt-5-mini-high", "gpt-5-medium"],
  price_checked: "2026-10-28" };

type Attempt = { model: string; outcome: string; error_category: string | null; latency_ms: number; retry: boolean };
const okAttempt = (model: string, latency = 800): Attempt =>
  ({ model, outcome: "ok", error_category: null, latency_ms: latency, retry: false });

function answer(opts: { text?: string; model?: string; category?: string; difficulty?: string; cost?: number;
                        compare?: boolean; attempts?: Attempt[]; tokens?: [number, number];
                        simulated?: boolean } = {}) {
  const attempts = opts.attempts ?? [okAttempt(opts.model ?? "gpt-5-nano-minimal")];
  const routed = attempts[attempts.length - 1].model;
  const [inTok, outTok] = opts.tokens ?? [40, 20];
  return {
    id: "rtr-1", object: "chat.completion", model: routed,
    choices: [{ index: 0, message: { role: "assistant", content: opts.text ?? "Intent: card_payment_charged_twice." },
                finish_reason: "stop" }],
    usage: { prompt_tokens: inTok, completion_tokens: outTok, total_tokens: inTok + outTok },
    router: {
      routed_to: routed, provider_model: `openai/${routed}`, reasoning_effort: null,
      category: opts.category ?? "complaint", difficulty: opts.difficulty ?? "easy", confidence: null,
      reason: "rules: complaint, easy (no difficulty signals) → gpt-5-nano-minimal", policy_version: "rules-1",
      mode: "balanced", forced: false, compare: !!opts.compare, signals: [], downgraded: false,
      fallbacks_used: attempts.length - 1, attempts, schema_valid: null, estimated_cost_usd: 0.0004,
      cost_usd: opts.cost ?? 0.000048, router_overhead_usd: 0, reference_model: "gpt-5-medium",
      cost_basis: opts.simulated ? "simulated" : "calculated", price_checked: "2026-10-28",
      reference_cost_usd: 0.00025, latency_ms: 950,
    },
  };
}

interface Mocks { ask?: (route: Route, body: Record<string, any>) => Promise<void> | void; healthDown?: boolean; demo?: boolean }

async function mock(page: Page, m: Mocks = {}) {
  const asked: Record<string, any>[] = [];
  await page.route(`${API}/api/health`, (r) => m.healthDown ? r.abort()
    : r.fulfill({ json: { ...health, demo_mode: !!m.demo } }));
  await page.route(`${API}/api/models`, (r) => r.fulfill({ json: { price_checked: "2026-10-28", options: [
    { id: "gpt-4.1", provider_model: "openai/gpt-4.1", tier: "strong", reasoning_effort: null,
      input_usd_per_mtok: 2, output_usd_per_mtok: 8, demo_allowed: true },
    { id: "gpt-5-nano-minimal", provider_model: "openai/gpt-5-nano", tier: "tiny", reasoning_effort: "minimal",
      input_usd_per_mtok: 0.05, output_usd_per_mtok: 0.4, demo_allowed: true },
    { id: "gpt-5-medium", provider_model: "openai/gpt-5", tier: "reasoning", reasoning_effort: "medium",
      input_usd_per_mtok: 1.25, output_usd_per_mtok: 10, demo_allowed: false, reference: true },
    { id: "gpt-5-search", provider_model: "openai/gpt-5-search-api", tier: "strong",
      reasoning_effort: null, input_usd_per_mtok: 1.25, output_usd_per_mtok: 10, per_call_usd: 0.01,
      web_search: true, demo_allowed: true },
  ] } }));
  await page.route(`${API}/api/stats`, (r) => r.fulfill({ json: {
    requests: 4, answered_pct: 75, total_cost_usd: 0.0003, avg_cost_per_answer_usd: 0.0001, compare_runs: 2,
    compare_spend_usd: 0.004, reference_cost_usd: 0.0015, spent_today_usd: 0.0043, latency_p50_ms: 900,
    latency_p95_ms: 2000, fallback_rate_pct: 33.3, requests_by_model: { "gpt-5-nano-minimal": 2, "gpt-5-mini-high": 1 },
    requests_by_category: { complaint: 2, loan_maths: 1 }, errors_by_status: { all_models_failed: 1 },
    cost_basis: m.demo ? "simulated" : "calculated", price_checked: "2026-10-28" } }));
  await page.route(`${API}/v1/chat/completions`, async (r) => {
    const body = r.request().postDataJSON();
    asked.push(body);
    if (m.ask) return m.ask(r, body);
    if (body.router.compare_with_reference) {
      return r.fulfill({ json: answer({ model: "gpt-5-medium", compare: true, cost: 0.0012, tokens: [40, 120],
                                         text: "Strong answer." }) });
    }
    return r.fulfill({ json: answer() });
  });
  return asked;
}

const own = (page: Page) => page.getByLabel("Or ask your own question");

async function ask(page: Page, text: string) {
  await own(page).fill(text);
  await page.getByRole("button", { name: "Route request" }).click();
}

test("explains what the site is and how to try it", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await expect(page.getByText("Expensive AI models often answer questions a cheap one could handle.")).toBeVisible();
  await expect(page.getByText("Try it:")).toBeVisible();
  await expect(page.getByTestId("connection")).toContainText("Router online");
  await expect(page.getByTestId("connection")).toContainText("6 OpenAI models to choose from");
  await expect(page.getByTestId("connection")).toContainText("Routing today: simple rules");
  await expect(page.getByTestId("demo-note")).toHaveCount(0);
});

test("says clearly when the router is not running", async ({ page }) => {
  await mock(page, { healthDown: true });
  await page.goto("/");
  await expect(page.getByTestId("connection")).toContainText("Router not reachable");
});

test("an example pair button asks the question straight away", async ({ page }) => {
  const asked = await mock(page);
  await page.goto("/");
  const pair = page.locator(".pair").filter({ hasText: "Loan maths" });
  await pair.getByRole("button", { name: /Hard/ }).click();
  await expect(page.getByTestId("answer")).toBeVisible();
  const body = asked[0];
  expect(body.model).toBe("auto");
  expect(body.messages[0].role).toBe("system");
  expect(body.messages[0].content).toContain("Answer briefly");
  expect(body.messages[1].content).toContain("prepay Rs 5 lakh");
  await expect(own(page)).toHaveValue(/prepay Rs 5 lakh/);
});

test("every pair has an easy and a hard question", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  const pairs = page.locator(".pair");
  await expect(pairs).toHaveCount(4);
  for (const i of [0, 1, 2, 3]) {
    await expect(pairs.nth(i).getByRole("button", { name: /^Easy/ })).toBeVisible();
    await expect(pairs.nth(i).getByRole("button", { name: /^Hard/ })).toBeVisible();
  }
});

test("shows the category and difficulty the router decided", async ({ page }) => {
  await mock(page, { ask: (r) => r.fulfill({ json: answer({ model: "gpt-5-mini-high", category: "loan_maths",
                                                            difficulty: "hard" }) }) });
  await page.goto("/");
  await ask(page, "hard loan question");
  await expect(page.getByTestId("trace-stop").nth(1)).toContainText("loan maths, hard");
  await expect(page.getByTestId("trace-stop").nth(1)).toContainText("rules-1");
  await expect(page.getByRole("row", { name: /hard loan question/ })).toContainText("loan maths, hard");
});

test("renders Markdown answers instead of raw symbols", async ({ page }) => {
  await mock(page, { ask: (r) => r.fulfill({ json: answer({ text: "**51 months sooner**\n\n1. EMI\n2. Balance\n\n`code`" }) }) });
  await page.goto("/");
  await ask(page, "hello");
  const a = page.getByTestId("answer");
  await expect(a.locator("strong")).toHaveText("51 months sooner");
  await expect(a.locator("ol li")).toHaveCount(2);
  await expect(a).not.toContainText("**");
});

test("receipt shows the cost and the estimated saving", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await ask(page, "hello");
  await expect(page.getByTestId("cost")).toHaveText("$0.000048");
  await expect(page.getByTestId("saving")).toHaveText("About 81% cheaper than gpt-5-medium would be");
  await expect(page.getByTestId("cost-basis")).toContainText("Calculated from actual token usage");
  await expect(page.getByTestId("cost-basis")).toContainText("2026-10-28");
});

test("demo-mode costs are labelled simulated everywhere", async ({ page }) => {
  await mock(page, { demo: true, ask: (r) => r.fulfill({ json: answer({ simulated: true }) }) });
  await page.goto("/");
  await ask(page, "hello");
  await expect(page.getByText("This request (simulated)")).toBeVisible();
  await expect(page.getByTestId("cost-basis")).toContainText("Simulated");
  await page.getByRole("tab", { name: "Usage" }).click();
  await expect(page.getByTestId("usage-basis")).toContainText("Simulated");
});

test("compare shows measured usage for both models side by side", async ({ page }) => {
  const asked = await mock(page);
  await page.goto("/");
  await ask(page, "Tag this complaint");
  await page.getByRole("button", { name: "Compare with gpt-5-medium" }).click();
  const table = page.getByTestId("compare-table");
  await expect(table).toContainText("gpt-5-nano-minimal");
  await expect(table).toContainText("gpt-5-medium");
  await expect(table.getByRole("row", { name: /Output tokens/ })).toContainText("120");
  await expect(page.getByTestId("compare-summary")).toContainText("The routed answer cost 96% less.");
  await expect(page.getByText("Strong answer.")).toBeVisible();
  expect(asked[1].router).toEqual({ compare_with_reference: true });
  expect(asked[1].messages[1].content).toBe("Tag this complaint");
});

test("no compare button when the strongest model already answered", async ({ page }) => {
  await mock(page, { ask: (r) => r.fulfill({ json: answer({ model: "gpt-5-medium" }) }) });
  await page.goto("/");
  await ask(page, "hello");
  await expect(page.getByTestId("answer")).toBeVisible();
  await expect(page.getByRole("button", { name: /Compare with/ })).toHaveCount(0);
});

test("sends the chosen mode and cost limit", async ({ page }) => {
  const asked = await mock(page);
  await page.goto("/");
  await page.getByText("Quality", { exact: true }).click();
  await page.getByLabel("Cost limit (USD, optional)").fill("0.01");
  await ask(page, "hello");
  await expect(page.getByTestId("answer")).toBeVisible();
  expect(asked[0].router).toEqual({ mode: "quality", max_cost_usd: 0.01 });
});

test("blocks an invalid cost limit", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await own(page).fill("hello");
  await page.getByLabel("Cost limit (USD, optional)").fill("abc");
  await expect(page.getByRole("button", { name: "Route request" })).toBeDisabled();
});

test("draws retries and fallbacks in the trace", async ({ page }) => {
  const attempts = [
    { model: "gpt-4.1-mini", outcome: "error", error_category: "rate_limit", latency_ms: 400, retry: false },
    { model: "gpt-4.1-mini", outcome: "error", error_category: "rate_limit", latency_ms: 400, retry: true },
    okAttempt("gpt-5-mini-low", 600),
  ];
  await mock(page, { ask: (r) => r.fulfill({ json: answer({ attempts }) }) });
  await page.goto("/");
  await ask(page, "EMI please");
  const stops = page.getByTestId("trace-stop");
  await expect(stops).toHaveCount(6);
  await expect(stops.nth(2)).toContainText("rate limited");
  await expect(stops.nth(3)).toContainText("retry");
  await expect(stops.nth(4)).toContainText("fallback");
  await expect(stops.nth(4)).toContainText("answered in 600 ms");
});

test("explains a cost limit that is too low", async ({ page }) => {
  await mock(page, { ask: (r) => r.fulfill({ status: 400, json: { error: {
    code: "budget_too_low", message: "No qualifying model fits", cheapest_qualifying_model: "gpt-4.1-mini",
    cheapest_estimated_cost_usd: 0.001316 } } }) });
  await page.goto("/");
  await ask(page, "hello");
  const error = page.getByTestId("error");
  await expect(error).toContainText("No model fits that cost limit");
  await expect(error).toContainText("gpt-4.1-mini");
  await expect(error).toContainText("$0.001316");
});

test("shows every failed attempt when no model answers", async ({ page }) => {
  await mock(page, { ask: (r) => r.fulfill({ status: 503, json: { error: {
    code: "all_models_failed", message: "Every model in the fallback chain failed.", request_id: "rtr-x",
    attempts: [
      { model: "gpt-4.1-mini", outcome: "error", error_category: "server_error", latency_ms: 300, retry: false },
      { model: "gpt-4.1", outcome: "skipped_breaker", error_category: null, latency_ms: 0, retry: false },
    ] } } }) });
  await page.goto("/");
  await ask(page, "hello");
  await expect(page.getByTestId("error")).toContainText("No model answered");
  await expect(page.getByTestId("trace-stop")).toHaveCount(5);
  await expect(page.getByTestId("trace-stop").nth(3)).toContainText("skipped: failing recently");
  await expect(page.getByTestId("trace-stop").last()).toContainText("No answer");
});

test("keeps a history and lets you reopen an earlier request", async ({ page }) => {
  let n = 0;
  await mock(page, { ask: (r) => { n += 1; return r.fulfill({ json: answer({ text: `answer ${n}` }) }); } });
  await page.goto("/");
  await ask(page, "first question");
  await expect(page.getByTestId("answer")).toHaveText("answer 1");
  await ask(page, "second question");
  await expect(page.getByTestId("answer")).toHaveText("answer 2");
  await page.getByRole("row", { name: /first question/ }).click();
  await expect(page.getByTestId("answer")).toHaveText("answer 1");
});

test("demo mode shows its note and the outage example", async ({ page }) => {
  await mock(page, { demo: true });
  await page.goto("/");
  await expect(page.getByTestId("demo-note")).toBeVisible();
  await expect(page.getByRole("button", { name: "Simulate an outage (demo mode)" })).toBeVisible();
});

test("outage example is hidden outside demo mode", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: /Simulate an outage/ })).toHaveCount(0);
});

test("models tab lists options cheapest first", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "Models" }).click();
  const rows = page.getByTestId("model-row");
  await expect(rows).toHaveCount(4);
  await expect(rows.first()).toContainText("gpt-5-nano-minimal");
  await expect(rows.filter({ hasText: "gpt-5-medium" })).toContainText("Compare only");
  await expect(page.getByText("web search, +$0.010 per search")).toBeVisible();
});

test("usage tab shows average cost, request types and compare spend", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "Usage" }).click();
  await expect(page.getByTestId("requests")).toHaveText("4");
  await expect(page.getByTestId("avg-cost")).toHaveText("$0.000100");
  await expect(page.getByTestId("by-category")).toContainText("customer complaint: 2");
  await expect(page.getByText("Compare runs: 2")).toBeVisible();
  await expect(page.getByText(/80% cheaper than the reference model would have been/)).toBeVisible();
  await expect(page.getByText(/Not a benchmark result/)).toBeVisible();
});

test("ctrl+enter sends the request", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await own(page).fill("quick one");
  await own(page).press("Control+Enter");
  await expect(page.getByTestId("answer")).toBeVisible();
});

test("the page never scrolls sideways, even with results, compare and history", async ({ page }) => {
  await mock(page, { ask: (r, body) => r.fulfill({ json: body.router.compare_with_reference
    ? answer({ model: "gpt-5-medium", compare: true, text: "| a | b | c | d |\n|---|---|---|---|\n| 1 | 2 | 3 | 4 |" })
    : answer({ text: "```sql\nSELECT merchant_name, SUM(amount) OVER (PARTITION BY city ORDER BY amount DESC) FROM t;\n```" }) }) });
  await page.goto("/");
  await ask(page, "A long question about merchants per city with shares and ties ".repeat(3));
  await page.getByRole("button", { name: /Compare with/ }).click();
  await expect(page.getByTestId("compare-table")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
