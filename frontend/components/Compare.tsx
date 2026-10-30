"use client";

import { useState } from "react";
import { ApiError, compareWithReference } from "@/lib/api";
import type { ChatResponse } from "@/lib/types";
import { costBasisNote, ms, usd } from "@/lib/format";
import Answer from "./Answer";

/** Runs the same question on the reference (strongest) model and shows measured usage side by side.
 *  No quality score is shown: for a visitor's own question there is no correct answer to grade against,
 *  so the reader judges the two answers. Scored comparisons come with benchmark mode. */
export default function Compare({ prompt, routed }: { prompt: string; routed: ChatResponse }) {
  const [strong, setStrong] = useState<ChatResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reference = routed.router.reference_model;

  if (routed.router.routed_to === reference) return null;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setStrong(await compareWithReference(prompt));
    } catch (e) {
      setError(e instanceof ApiError ? e.body.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!strong) {
    return (
      <div className="compare-cta">
        <button type="button" className="secondary" onClick={run} disabled={busy}>
          {busy ? `Asking ${reference}…` : `Compare with ${reference}`}
        </button>
        <span>Ask the strongest model the same question and judge whether the cheaper answer was good enough.</span>
        {error && <p className="bad" role="alert">{error}</p>}
      </div>
    );
  }

  const cols = [{ title: "Routed", r: routed }, { title: "Strongest", r: strong }];
  const a = routed.router;
  const b = strong.router;
  const cheaper = b.cost_usd > 0 ? Math.round((1 - a.cost_usd / b.cost_usd) * 100) : null;

  return (
    <section className="compare" aria-label="Comparison with the strongest model">
      <h2>Routed answer vs. strongest model</h2>
      <table className="compare-table" data-testid="compare-table">
        <thead>
          <tr><th>{a.cost_basis === "simulated" ? "Simulated" : "Measured"}</th><th className="num">Routed</th><th className="num">Strongest</th></tr>
        </thead>
        <tbody>
          <tr><td>Model</td><td className="num mono">{a.routed_to}</td><td className="num mono">{b.routed_to}</td></tr>
          <tr><td>Input tokens</td><td className="num">{routed.usage.prompt_tokens}</td><td className="num">{strong.usage.prompt_tokens}</td></tr>
          <tr><td>Output tokens</td><td className="num">{routed.usage.completion_tokens}</td><td className="num">{strong.usage.completion_tokens}</td></tr>
          <tr><td>Cost</td><td className="num">{usd(a.cost_usd)}</td><td className="num">{usd(b.cost_usd)}</td></tr>
          <tr><td>Latency</td><td className="num">{ms(a.latency_ms)}</td><td className="num">{ms(b.latency_ms)}</td></tr>
        </tbody>
      </table>
      {cheaper !== null && (
        <p className="compare-summary" data-testid="compare-summary">
          {cheaper >= 0 ? `The routed answer cost ${cheaper}% less.` : `The routed answer cost ${-cheaper}% more.`}{" "}
          Quality is yours to judge: read both answers.
        </p>
      )}
      <p className="basis">{costBasisNote(a.cost_basis, a.price_checked)} One question is not a benchmark: measured savings
        across many questions come with the evaluation report.</p>
      <div className="compare-cols">
        {cols.map(({ title, r }) => (
          <article key={title} className="compare-col">
            <h3>{title}: <span className="mono">{r.router.routed_to}</span></h3>
            <Answer text={r.choices[0].message.content} />
          </article>
        ))}
      </div>
    </section>
  );
}
