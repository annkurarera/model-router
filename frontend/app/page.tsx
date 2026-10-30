"use client";

import { useEffect, useState } from "react";
import Playground from "@/components/Playground";
import ModelsView from "@/components/ModelsView";
import UsageView from "@/components/UsageView";
import { ApiError, getHealth, ROUTER_URL } from "@/lib/api";
import type { Health, HistoryEntry } from "@/lib/types";

type Tab = "playground" | "models" | "usage";
const TABS: { id: Tab; label: string }[] = [
  { id: "playground", label: "Playground" },
  { id: "models", label: "Models" },
  { id: "usage", label: "Usage" },
];

export default function Home() {
  const [tab, setTab] = useState<Tab>("playground");
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  useEffect(() => {
    getHealth().then((h) => { setHealth(h); setOffline(false); })
      .catch((e: ApiError) => { setOffline(true); void e; });
  }, []);

  return (
    <main className="page">
      <header className="masthead">
        <h1>Model Router</h1>
        <p className="intro">
          Expensive AI models often answer questions a cheap one could handle. This router reads each
          question, judges how hard it is, and sends it to the cheapest model that can do it well.
        </p>
        <p className="how">
          <strong>Try it:</strong> run an easy question and a hard one below, then look at which model
          answered each and what it cost. Use <em>Compare</em> to check the cheap answer against the strongest model.
        </p>
        <p className="connection" data-testid="connection">
          {health ? (
            <>
              <span className="lamp go" aria-hidden="true" />Router online
              <span className="sep">{health.models_enabled.length} OpenAI models to choose from</span>
              <span className="sep">Routing today: simple rules. Rules tuned on benchmark results come next.</span>
            </>
          ) : offline ? (
            <><span className="lamp stop" aria-hidden="true" />Router not reachable at <span className="mono">{ROUTER_URL}</span></>
          ) : (
            <><span className="lamp" aria-hidden="true" />Checking the router</>
          )}
        </p>
        {health?.demo_mode && (
          <p className="demo-note" data-testid="demo-note">
            Demo mode: answers are scripted and cost nothing. Routing, retries, fallbacks, limits and cost
            arithmetic are the real thing.
          </p>
        )}
      </header>

      <nav className="tabs" role="tablist" aria-label="Views">
        {TABS.map((t) => (
          <button key={t.id} role="tab" type="button" aria-selected={tab === t.id}
                  className={tab === t.id ? "tab on" : "tab"} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>

      <div role="tabpanel">
        {tab === "playground" && (
          <Playground demoMode={!!health?.demo_mode} history={history}
                      onResult={(entry) => setHistory((h) => [entry, ...h].slice(0, 50))} />
        )}
        {tab === "models" && <ModelsView />}
        {tab === "usage" && <UsageView />}
      </div>
    </main>
  );
}
