import type { ChatResponse } from "@/lib/types";
import { costBasisNote, ms, per1000, savedPct, usd } from "@/lib/format";

/** What the request cost, compared with sending it to the reference (strongest) model. */
export default function Receipt({ response }: { response: ChatResponse }) {
  const r = response.router;
  const saved = savedPct(r.cost_usd, r.reference_cost_usd);
  return (
    <aside className="receipt" aria-label="Cost and routing details">
      <dl className="receipt-figures">
        <div>
          <dt>This request{r.cost_basis === "simulated" ? " (simulated)" : ""}</dt>
          <dd data-testid="cost">{usd(r.cost_usd)}</dd>
          <dd className="small">{per1000(r.cost_usd)} per 1,000 like it</dd>
        </div>
        <div>
          <dt>On {r.reference_model}</dt>
          <dd>{usd(r.reference_cost_usd)}</dd>
          <dd className="small">same tokens, reference model prices</dd>
        </div>
      </dl>
      {saved !== null && (
        <p className={saved >= 0 ? "saving" : "saving over"} data-testid="saving">
          {saved >= 0 ? `About ${saved}% cheaper than ${r.reference_model} would be` : `About ${-saved}% dearer than ${r.reference_model} would be`}
        </p>
      )}
      <dl className="receipt-rows">
        <div><dt>Model</dt><dd className="mono">{r.routed_to}</dd></div>
        <div><dt>Reasoning effort</dt><dd>{r.reasoning_effort ?? "none"}</dd></div>
        <div><dt>Latency</dt><dd>{ms(r.latency_ms)}</dd></div>
        <div><dt>Tokens in / out</dt><dd>{response.usage.prompt_tokens} / {response.usage.completion_tokens}</dd></div>
        <div><dt>Fallbacks</dt><dd>{r.fallbacks_used}</dd></div>
        <div><dt>Estimated before calling</dt><dd>{usd(r.estimated_cost_usd)}</dd></div>
      </dl>
      <p className="reason"><span>Why this model:</span> {r.reason}</p>
      <p className="basis" data-testid="cost-basis">{costBasisNote(r.cost_basis, r.price_checked)}</p>
    </aside>
  );
}
