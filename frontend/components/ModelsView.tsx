"use client";

import { useEffect, useState } from "react";
import { ApiError, getModels } from "@/lib/api";
import type { ModelOption } from "@/lib/types";

const TIER_WORDS: Record<ModelOption["tier"], string> = {
  tiny: "Tiny", mid: "Mid", strong: "Strong", reasoning: "Reasoning",
};

/** The pool the router chooses from, cheapest output first. */
export default function ModelsView() {
  const [data, setData] = useState<{ price_checked: string; options: ModelOption[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getModels().then(setData).catch((e: ApiError) => setError(e.body?.message ?? String(e)));
  }, []);

  if (error) return <p className="failure" role="alert">{error}</p>;
  if (!data) return <p className="status">Loading models…</p>;

  const rows = [...data.options].sort((a, b) => a.output_usd_per_mtok - b.output_usd_per_mtok);
  return (
    <section className="table-view" aria-label="Model pool">
      <h2>Model pool</h2>
      <p className="lede">
        Each row is one routing option. The same model at a different reasoning effort is a separate option,
        because it costs and waits differently. Prices are US dollars per million tokens, checked {data.price_checked}.
      </p>
      <table>
        <thead>
          <tr>
            <th>Option</th><th>Tier</th><th>Reasoning effort</th>
            <th className="num">Input</th><th className="num">Output</th><th>Public demo</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id} data-testid="model-row">
              <td>
                <span className="mono">{o.id}</span>
                {o.web_search && (
                  <span className="small-note">web search, +${(o.per_call_usd ?? 0).toFixed(3)} per search</span>
                )}
              </td>
              <td>{TIER_WORDS[o.tier]}</td>
              <td>{o.reasoning_effort ?? "none"}</td>
              <td className="num">${o.input_usd_per_mtok.toFixed(2)}</td>
              <td className="num">${o.output_usd_per_mtok.toFixed(2)}</td>
              <td>{o.demo_allowed ? "Yes" : o.reference ? "Compare only" : "Admin only"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="basis">
        Compare only: the router never picks this model for a question, but the Compare button asks it the same
        question so you can judge the cheaper answer. Web-search models answer only live questions (weather, news,
        today&apos;s prices) and cost a fixed fee per search on top of tokens.
      </p>
    </section>
  );
}
