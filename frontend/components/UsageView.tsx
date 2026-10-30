"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, getStats } from "@/lib/api";
import type { Stats } from "@/lib/types";
import { categoryWords, costBasisNote, ms, savedPct, usd } from "@/lib/format";

/** Totals across everyone's requests since the router's log was created. */
export default function UsageView() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getStats().then((s) => { setStats(s); setError(null); })
      .catch((e: ApiError) => setError(e.body?.message ?? String(e)));
  }, []);
  useEffect(load, [load]);

  if (error) return <p className="failure" role="alert">{error}</p>;
  if (!stats) return <p className="status">Loading usage…</p>;
  if (stats.requests === 0) return <p className="empty">No requests yet. Route one from the playground.</p>;

  const saved = savedPct(stats.total_cost_usd, stats.reference_cost_usd);
  const byModel = Object.entries(stats.requests_by_model).sort((a, b) => b[1] - a[1]);
  const byCategory = Object.entries(stats.requests_by_category ?? {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...byModel.map(([, n]) => n), 1);
  const errors = Object.entries(stats.errors_by_status);

  return (
    <section className="usage" aria-label="Usage">
      <div className="usage-head">
        <h2>Usage</h2>
        <button type="button" className="quiet" onClick={load}>Refresh</button>
      </div>
      <p className="basis" data-testid="usage-basis">{costBasisNote(stats.cost_basis, stats.price_checked)}</p>
      <dl className="figures">
        <div><dt>Requests</dt><dd data-testid="requests">{stats.requests}</dd></div>
        <div><dt>Answered</dt><dd>{stats.answered_pct ?? 0}%</dd></div>
        <div><dt>Spent on routed requests</dt><dd>{usd(stats.total_cost_usd)}</dd></div>
        <div><dt>Average cost per answer</dt><dd data-testid="avg-cost">{usd(stats.avg_cost_per_answer_usd)}</dd></div>
        <div><dt>Same answers on the reference model</dt><dd>{usd(stats.reference_cost_usd)}</dd></div>
        <div><dt>Latency p50 / p95</dt><dd>{ms(stats.latency_p50_ms)} / {ms(stats.latency_p95_ms)}</dd></div>
        <div><dt>Needed a fallback</dt><dd>{stats.fallback_rate_pct ?? 0}%</dd></div>
      </dl>
      {saved !== null && (
        <p className="saving">{saved >= 0 ? `${saved}% cheaper` : `${-saved}% dearer`} than the reference model would have been for the
          same tokens, on the questions asked here. Not a benchmark result.</p>
      )}

      <h3>Requests by model</h3>
      <ul className="bars">
        {byModel.map(([model, n]) => (
          <li key={model}>
            <span className="mono bar-label">{model}</span>
            <span className="bar" style={{ width: `${(n / max) * 100}%` }} />
            <span className="bar-value">{n}</span>
          </li>
        ))}
      </ul>

      {byCategory.length > 0 && (
        <>
          <h3>Answered requests by type</h3>
          <ul className="error-list" data-testid="by-category">
            {byCategory.map(([c, n]) => <li key={c}>{categoryWords(c)}: {n}</li>)}
          </ul>
        </>
      )}

      {stats.compare_runs > 0 && (
        <p className="lede">
          Compare runs: {stats.compare_runs}, costing {usd(stats.compare_spend_usd)}. They are left out of the figures above.
        </p>
      )}

      {errors.length > 0 && (
        <>
          <h3>Requests without an answer</h3>
          <ul className="error-list">
            {errors.map(([code, n]) => <li key={code}><span className="mono">{code}</span> {n}</li>)}
          </ul>
        </>
      )}
    </section>
  );
}
