import type { Attempt, Mode, RouterMeta } from "@/lib/types";
import { categoryWords, errorWords, ms } from "@/lib/format";

type Signal = "go" | "caution" | "stop" | "idle";

interface Stop {
  key: string;
  title: string;
  mono?: boolean;
  lines: string[];
  signal: Signal;     // colour of this stop's marker
  track: Signal;      // colour of the track leading INTO this stop
  tag?: string;
}

interface Props {
  mode: Mode;
  meta?: RouterMeta;
  attempts?: Attempt[];
  failedCode?: string;
  totalLatencyMs?: number;
}

function attemptStop(a: Attempt, i: number, prev?: Attempt): Stop {
  const intoTrack: Signal = prev && prev.outcome !== "ok" ? "caution" : "idle";
  const tag = a.retry ? "retry" : prev && prev.model !== a.model && prev.outcome !== "ok" ? "fallback" : undefined;
  if (a.outcome === "ok") {
    return { key: `a${i}`, title: a.model, mono: true, lines: [`answered in ${ms(a.latency_ms)}`],
             signal: "go", track: intoTrack, tag };
  }
  if (a.outcome === "error") {
    return { key: `a${i}`, title: a.model, mono: true, lines: [`${errorWords(a.error_category)}`, ms(a.latency_ms)],
             signal: "stop", track: intoTrack, tag };
  }
  const why = a.outcome === "skipped_breaker" ? "skipped: failing recently" : "skipped: over cost limit";
  return { key: `a${i}`, title: a.model, mono: true, lines: [why], signal: "caution", track: intoTrack, tag };
}

/** The route one request took: request, policy, every model attempt, and the outcome. */
export default function RouteTrace({ mode, meta, attempts, failedCode, totalLatencyMs }: Props) {
  const list = meta?.attempts ?? attempts ?? [];
  const answered = !!meta;
  const policyLines = meta
    ? [meta.category ? `${categoryWords(meta.category)}, ${meta.difficulty}` : "same models for every request",
       meta.policy_version]
    : ["no decision recorded"];

  const stops: Stop[] = [
    { key: "req", title: "Request", lines: [`${mode} mode`], signal: "idle", track: "idle" },
    { key: "pol", title: "Policy", lines: policyLines, signal: "idle", track: "idle",
      tag: meta?.downgraded ? "downgraded" : undefined },
    ...list.map((a, i) => attemptStop(a, i, list[i - 1])),
    answered
      ? { key: "end", title: "Answer", lines: [`total ${ms(totalLatencyMs ?? meta.latency_ms)}`],
          signal: "go", track: "go" }
      : { key: "end", title: "No answer", lines: [failedCode ? failedCode.replace(/_/g, " ") : "stopped"],
          signal: "stop", track: "stop" },
  ];

  const label = answered
    ? `Routed to ${meta!.routed_to} after ${list.length} attempt${list.length === 1 ? "" : "s"}`
    : `No model answered after ${list.length} attempt${list.length === 1 ? "" : "s"}`;

  return (
    <figure className="trace" aria-label={label}>
      <ol className="trace-line">
        {stops.map((s, i) => (
          <li key={s.key} className={`stop signal-${s.signal} track-${s.track}`}
              style={{ ["--i" as string]: i }} data-testid="trace-stop">
            <span className="marker" aria-hidden="true" />
            <span className={s.mono ? "stop-title mono" : "stop-title"}>{s.title}</span>
            {s.tag && <span className={`tag tag-${s.tag}`}>{s.tag}</span>}
            {s.lines.map((line) => (
              <span key={line} className="stop-line">{line}</span>
            ))}
          </li>
        ))}
      </ol>
      <figcaption className="sr-only">{label}</figcaption>
    </figure>
  );
}
