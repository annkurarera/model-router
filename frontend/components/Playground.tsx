"use client";

import { useState } from "react";
import { ApiError, routeRequest } from "@/lib/api";
import type { HistoryEntry, Mode } from "@/lib/types";
import { categoryWords, clock, ms, usd } from "@/lib/format";
import RouteTrace from "./RouteTrace";
import Receipt from "./Receipt";
import Answer from "./Answer";
import Compare from "./Compare";
import ExamplePairs from "./ExamplePairs";

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: "cheapest", label: "Cheapest", hint: "treats every question as one level easier" },
  { value: "balanced", label: "Balanced", hint: "routes by the judged difficulty" },
  { value: "quality", label: "Quality", hint: "treats every question as one level harder" },
];

interface Props {
  demoMode: boolean;
  history: HistoryEntry[];
  onResult: (entry: HistoryEntry) => void;
}

export default function Playground({ demoMode, history, onResult }: Props) {
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<Mode>("balanced");
  const [costLimit, setCostLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const current = history.find((h) => h.key === selected) ?? history[0];
  const limitValue = costLimit.trim() === "" ? null : Number(costLimit);
  const limitInvalid = limitValue !== null && (!Number.isFinite(limitValue) || limitValue <= 0);

  async function send(textArg?: string) {
    const text = (textArg ?? prompt).trim();
    if (!text || busy || limitInvalid) return;
    setBusy(true);
    const key = `${Date.now()}`;
    const entry: HistoryEntry = { key, prompt: text, at: new Date(), mode };
    try {
      entry.response = await routeRequest(text, mode, limitValue);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, { code: "unexpected", message: String(e) });
      entry.error = err.body;
      entry.status = err.status;
    } finally {
      setBusy(false);
    }
    onResult(entry);
    setSelected(key);
  }

  return (
    <div className="playground">
      <ExamplePairs onPick={(e) => { setPrompt(e.prompt); send(e.prompt); }} />
      {demoMode && (
        <button type="button" className="link" onClick={() => {
          const t = "What is the EMI on a Rs 10 lakh loan at 10% for 5 years? #outage";
          setPrompt(t); send(t);
        }}>Simulate an outage (demo mode)</button>
      )}

      <section className="composer" aria-label="Ask your own question">
        <label htmlFor="prompt" className="field-label">Or ask your own question</label>
        <textarea
          id="prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send();
          }}
          placeholder="Anything a bank's operations team might ask: a complaint to sort, a loan calculation, fields to pull from a document, SQL or Python to write."
          rows={3}
          maxLength={6000}
        />
        <div className="controls">
          <fieldset className="modes">
            <legend className="field-label">Mode</legend>
            {MODES.map((m) => (
              <label key={m.value} className={mode === m.value ? "mode on" : "mode"} title={m.hint}>
                <input type="radio" name="mode" value={m.value} checked={mode === m.value}
                       onChange={() => setMode(m.value)} />
                {m.label}
              </label>
            ))}
          </fieldset>
          <div className="limit">
            <label htmlFor="limit" className="field-label">Cost limit (USD, optional)</label>
            <input id="limit" inputMode="decimal" placeholder="0.01" value={costLimit}
                   onChange={(e) => setCostLimit(e.target.value)} aria-invalid={limitInvalid} />
          </div>
          <button type="button" className="send" onClick={() => send()} disabled={busy || !prompt.trim() || limitInvalid}>
            {busy ? "Routing…" : "Route request"}
          </button>
        </div>
        <p className="mode-note">
          Cheapest treats each question as one level easier, Quality as one level harder. Ctrl+Enter sends.
        </p>
      </section>

      {busy && (
        <p className="status" role="status">
          Choosing a model and waiting for its answer. Hard questions go to reasoning models, which can take up to a minute.
        </p>
      )}

      {current && (
        <section className="result" aria-label="Result" key={current.key}>
          <p className="asked"><span>Question</span> {current.prompt}</p>
          <RouteTrace mode={current.mode} meta={current.response?.router} attempts={current.error?.attempts}
                      failedCode={current.error?.code} totalLatencyMs={current.response?.router.latency_ms} />
          {current.response ? (
            <>
              <div className="answer-row">
                <div className="answer">
                  <h2>Answer</h2>
                  <Answer text={current.response.choices[0].message.content} />
                </div>
                <Receipt response={current.response} />
              </div>
              <Compare key={`c-${current.key}`} prompt={current.prompt} routed={current.response} />
            </>
          ) : (
            <div className="failure" role="alert" data-testid="error">
              <h2>{failureTitle(current.error?.code)}</h2>
              {current.error?.cheapest_qualifying_model ? (
                <p>The cheapest model that qualifies is <span className="mono">{current.error.cheapest_qualifying_model}</span>,
                  estimated at {usd(current.error.cheapest_estimated_cost_usd)} for this question. Raise the cost limit or clear it.</p>
              ) : (
                <p>{current.error?.message}</p>
              )}
            </div>
          )}
        </section>
      )}

      {history.length > 0 && (
        <section className="history" aria-label="This session's requests">
          <h2>This session</h2>
          <div className="table-scroll">
          <table>
            <thead>
              <tr><th className="col-time">Time</th><th>Question</th><th>Type</th><th>Model</th><th className="num">Cost</th>
                  <th className="num col-latency">Latency</th></tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.key} className={h.key === current?.key ? "current" : undefined}
                    onClick={() => setSelected(h.key)} tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setSelected(h.key)}>
                  <td className="col-time">{clock(h.at)}</td>
                  <td className="prompt-cell">{h.prompt}</td>
                  <td>{h.response?.router.category ? `${categoryWords(h.response.router.category)}, ${h.response.router.difficulty}` : "–"}</td>
                  <td className="mono">{h.response?.router.routed_to ?? <span className="bad">{h.error?.code}</span>}</td>
                  <td className="num">{h.response ? usd(h.response.router.cost_usd) : "–"}</td>
                  <td className="num col-latency">{h.response ? ms(h.response.router.latency_ms) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </section>
      )}
    </div>
  );
}

function failureTitle(code?: string): string {
  switch (code) {
    case "budget_too_low": return "No model fits that cost limit";
    case "rate_limited":
    case "daily_limit_reached": return "Too many requests";
    case "daily_budget_reached": return "Today's budget is used up";
    case "router_unreachable": return "Router not running";
    case "request_too_large": return "Question too long";
    default: return "No model answered";
  }
}
