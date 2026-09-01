/**
 * The agent SSE client.
 *
 * `EventSource` cannot be used here: it only issues GET requests, and the agent
 * takes a JSON body. So this reads the response stream from `fetch` and parses
 * SSE frames by hand — which also means we get an AbortController for free, and
 * the user can stop a run mid-flight.
 *
 * The parser is written for the real wire format rather than the ideal one:
 * frames can be split across chunk boundaries at any byte, and comment lines
 * (`: heartbeat`) must be skipped without being mistaken for data.
 */

import { useCallback, useRef, useState } from 'react';

export interface PlanStep {
  id: number;
  tool: string;
  why: string;
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped';
}

export interface TraceEvent {
  type: string;
  at: string;
  run_id?: string;
  [key: string]: unknown;
}

export interface AgentFinal {
  answer: string;
  risk: import('@/lib/types').RiskResult | null;
  ui_spec: {
    camera: { lat: number; lon: number; zoom: number };
    bbox: [number, number, number, number];
    layers: string[];
    charts: string[];
    card: string | null;
  } | null;
  plan: PlanStep[];
  evidence: import('@/lib/types').Evidence[];
  citations: import('@/lib/types').Citation[];
  evidence_summary: import('@/lib/types').EvidenceSummary;
  critic: { verdict: string | null; reason: string | null; rounds: number };
  llm_provider: string | null;
}

/** One sub-question the decomposer found, and the intent it was routed to. */
export interface SubQuestion {
  id: number;
  text: string;
  intent: string;
}

export interface Decomposition {
  method: string;
  parts: SubQuestion[];
  tools: string[];
}

export interface Localised {
  language: string;
  text: string;
  provider: string;
  /** Figures and verdict terms survived. This is the safety question. */
  ok: boolean;
  /** Every clause was translated. A false here with ok true means some clause
   *  stayed in English rather than being translated unsafely. */
  fullyTranslated: boolean;
  detail: string | null;
  guard: Record<string, unknown>;
}

export interface Spoken {
  ok: boolean;
  /** data: URI — playable with no second round trip. */
  audio: string | null;
  /** What was actually said, which is a summary of the written answer. */
  spokenText: string;
  summarised: boolean;
  language: string;
  speaker: string;
  durationS: number | null;
  nativeVoice: boolean;
  voiceNote: string | null;
  detail: string | null;
}

export interface AgentRun {
  runId: string | null;
  question: string;
  events: TraceEvent[];
  plan: PlanStep[];
  planRationale: string;
  final: AgentFinal | null;
  error: string | null;
  running: boolean;
  /** Set when the question turned out to be compound. */
  decomposition: Decomposition | null;
  /** The reply in the user's language, when one was asked for. */
  localised: Localised | null;
  /** The spoken advisory, when speech was asked for. */
  spoken: Spoken | null;
  /** Wall-clock ms since the run started, per event — this is what proves the
   *  trace is live rather than replayed from a fixture. */
  timings: Record<number, number>;
}

const EMPTY: AgentRun = {
  runId: null,
  question: '',
  events: [],
  plan: [],
  planRationale: '',
  final: null,
  error: null,
  running: false,
  decomposition: null,
  localised: null,
  spoken: null,
  timings: {},
};

export function useAgentStream() {
  const [run, setRun] = useState<AgentRun>(EMPTY);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRun((r) => ({ ...r, running: false }));
  }, []);

  const ask = useCallback(
    async (args: {
      question: string;
      lat: number;
      lon: number;
      loaM: number;
      place?: string;
      /** Reply in this language. The agent always reasons in English. */
      replyLanguage?: string;
      speak?: boolean;
    }) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const startedAt = performance.now();
      setRun({ ...EMPTY, question: args.question, running: true });

      const push = (event: TraceEvent) => {
        setRun((prev) => {
          const next: AgentRun = {
            ...prev,
            events: [...prev.events, event],
            timings: { ...prev.timings, [prev.events.length]: performance.now() - startedAt },
          };
          switch (event.type) {
            case 'run_started':
              next.runId = String(event.run_id ?? '');
              break;
            case 'plan':
              next.plan = (event.steps as PlanStep[]) ?? [];
              next.planRationale = String(event.rationale ?? '');
              break;
            case 'tool_call':
              // Mark the step running so the timeline shows a spinner on the
              // step actually in flight, not on all of them.
              next.plan = next.plan.map((s) =>
                s.id === event.step_id ? { ...s, status: 'running' } : s,
              );
              break;
            case 'tool_result':
              next.plan = next.plan.map((s) =>
                s.id === event.step_id ? { ...s, status: event.ok ? 'done' : 'failed' } : s,
              );
              break;
            case 'final':
              next.final = event as unknown as AgentFinal;
              if (Array.isArray(event.plan)) next.plan = event.plan as PlanStep[];
              break;
            case 'decomposition':
              next.decomposition = {
                method: String(event.method ?? 'unknown'),
                parts: (event.parts as SubQuestion[]) ?? [],
                tools: (event.tools as string[]) ?? [],
              };
              break;
            case 'translation':
              next.localised = {
                language: String(event.language ?? ''),
                text: String(event.text ?? ''),
                provider: String(event.provider ?? ''),
                ok: Boolean(event.ok),
                fullyTranslated: Boolean(event.fully_translated ?? event.ok),
                detail: event.detail ? String(event.detail) : null,
                guard: (event.guard as Record<string, unknown>) ?? {},
              };
              break;
            case 'audio':
              next.spoken = {
                ok: Boolean(event.ok),
                audio: event.audio ? String(event.audio) : null,
                spokenText: String(event.spoken_text ?? ''),
                summarised: Boolean(event.summarised),
                language: String(event.language ?? ''),
                speaker: String(event.speaker ?? ''),
                durationS: event.duration_s === null || event.duration_s === undefined
                  ? null
                  : Number(event.duration_s),
                nativeVoice: Boolean(event.native_voice),
                voiceNote: event.voice_note ? String(event.voice_note) : null,
                detail: event.detail ? String(event.detail) : null,
              };
              break;
            case 'error':
              next.error = String(event.message ?? 'the agent run failed');
              break;
          }
          return next;
        });
      };

      try {
        const response = await fetch('/api/agent/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
          body: JSON.stringify({
            question: args.question,
            lat: args.lat,
            lon: args.lon,
            loa_m: args.loaM,
            place: args.place,
            reply_language: args.replyLanguage,
            speak: args.speak ?? false,
          }),
        });

        if (!response.ok || !response.body) {
          throw new Error(`agent stream failed: HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // Frames are separated by a blank line. A chunk can end mid-frame, so
          // only complete frames are consumed and the remainder stays buffered.
          let split = buffer.indexOf('\n\n');
          while (split !== -1) {
            const frame = buffer.slice(0, split);
            buffer = buffer.slice(split + 2);
            const dataLines = frame
              .split('\n')
              .filter((line) => line.startsWith('data:'))
              .map((line) => line.slice(5).trim());
            if (dataLines.length > 0) {
              try {
                push(JSON.parse(dataLines.join('\n')) as TraceEvent);
              } catch {
                /* a malformed frame must not kill the stream */
              }
            }
            split = buffer.indexOf('\n\n');
          }
        }
      } catch (cause) {
        if ((cause as Error)?.name !== 'AbortError') {
          setRun((prev) => ({
            ...prev,
            error:
              cause instanceof Error
                ? cause.message
                : 'the agent stream failed for an unknown reason',
          }));
        }
      } finally {
        abortRef.current = null;
        setRun((prev) => ({ ...prev, running: false }));
      }
    },
    [],
  );

  const reset = useCallback(() => setRun(EMPTY), []);

  return { run, ask, stop, reset };
}
