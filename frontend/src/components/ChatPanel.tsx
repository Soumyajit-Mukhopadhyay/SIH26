/**
 * Chat, with the live agent trace beside it.
 *
 * The trace is the feature, not decoration. What it has to show, in this order,
 * because this is the order that makes the system legible:
 *
 * 1. **The plan, before anything runs.** An agent that publishes its intent is a
 *    different thing from a chatbot that answers, and this is where you see it.
 * 2. **Each tool call landing on its own**, with its real latency. Timings are
 *    measured client-side and displayed, because a trace with plausible
 *    timings is checkable and a trace without them is a screenshot.
 * 3. **The critic's ruling**, including a bounce. When the critic rejects a
 *    draft for softening a NO-GO, that is the single most convincing thing on
 *    screen, and it must be visible rather than hidden in a log.
 *
 * The panel deliberately shows failures. A tool that errored gets a red row with
 * its message, because an agent that visibly copes with a dead source is more
 * credible than one that only ever demos the happy path.
 */

import { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowUp,
  Ban,
  Check,
  ChevronRight,
  CircleDashed,
  Cpu,
  Gavel,
  ListChecks,
  Loader2,
  MessageSquare,
  Search,
  Split,
  Square,
  Wrench,
  X,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { AgentRun, PlanStep, TraceEvent } from '@/hooks/useAgentStream';
import { LocalisedAnswer } from '@/components/LocalisedAnswer';
import { VoiceBar, type VoiceOption } from '@/components/VoiceBar';
import { useSpeaker } from '@/hooks/useVoice';
import { inline, stripBullet } from '@/lib/markdown';

/**
 * Prototype UX flag: keep the full agent-trace implementation, but do not show
 * it in the chat panel. Flip to `true` to restore the detailed timeline.
 */
const SHOW_AGENT_TRACE = false;

const WORKING_MESSAGES = [
  'Searching coastal data…',
  'Working on your question…',
  'Checking conditions…',
  'Gathering verified evidence…',
];

const SUGGESTIONS = [
  'Is it safe to go out tomorrow morning?',
  'Where is the water warmest near here?',
  'What are the wave limits for my boat, and who says so?',
  'Where does your data actually come from?',
];

function WorkingMotion() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => {
      setIndex((current) => (current + 1) % WORKING_MESSAGES.length);
    }, 2200);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div
      className="border-hairline bg-abyss-0/40 mb-3 overflow-hidden rounded border px-3 py-4"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="flex items-center gap-3">
        <div className="relative flex h-9 w-9 shrink-0 items-center justify-center">
          <span className="border-cyan/30 absolute inset-0 rounded-full border" />
          <span className="border-cyan absolute inset-0 animate-ping rounded-full border opacity-40" />
          <Search className="text-cyan h-3.5 w-3.5 animate-pulse" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="label text-cyan mb-1">ORCA is working</div>
          <p
            key={index}
            className="text-ink-1 text-xs leading-snug"
            style={{ animation: 'orca-rise 280ms var(--ease-out-instrument)' }}
          >
            {WORKING_MESSAGES[index]}
          </p>
          <div className="mt-2.5 flex gap-1">
            {[0, 1, 2].map((dot) => (
              <span
                key={dot}
                className="bg-cyan/70 h-1 w-1 rounded-full"
                style={{
                  animation: 'orca-pulse 1.2s ease-in-out infinite',
                  animationDelay: `${dot * 0.2}s`,
                }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function StepIcon({ status }: { status: PlanStep['status'] }) {
  if (status === 'running') return <Loader2 className="text-cyan h-3 w-3 animate-spin" aria-hidden />;
  if (status === 'done') return <Check className="text-jade h-3 w-3" aria-hidden />;
  if (status === 'failed') return <X className="text-red h-3 w-3" aria-hidden />;
  return <CircleDashed className="text-ink-3 h-3 w-3" aria-hidden />;
}

function TraceRow({
  event,
  elapsedMs,
}: {
  event: TraceEvent;
  elapsedMs: number | undefined;
}) {
  const time = elapsedMs === undefined ? '' : `${(elapsedMs / 1000).toFixed(2)}s`;

  const shell = (
    icon: React.ReactNode,
    title: React.ReactNode,
    body?: React.ReactNode,
    tone: 'default' | 'good' | 'warn' | 'bad' = 'default',
  ) => (
    <div className="flex gap-2" style={{ animation: 'orca-rise 200ms var(--ease-out-instrument)' }}>
      <div className="flex w-9 shrink-0 justify-end pt-0.5">
        <span className="data text-ink-3 text-2xs">{time}</span>
      </div>
      <div className="flex w-4 shrink-0 justify-center pt-0.5">{icon}</div>
      <div className="min-w-0 flex-1 pb-2">
        <div
          className={clsx(
            'text-2xs leading-snug',
            tone === 'good'
              ? 'text-jade'
              : tone === 'warn'
                ? 'text-amber'
                : tone === 'bad'
                  ? 'text-red'
                  : 'text-ink-1',
          )}
        >
          {title}
        </div>
        {body && <div className="text-ink-2 mt-0.5 text-2xs leading-snug">{body}</div>}
      </div>
    </div>
  );

  switch (event.type) {
    case 'run_started':
      return shell(
        <Cpu className="text-cyan h-3 w-3" aria-hidden />,
        <span className="text-ink-0">Run started</span>,
        <span className="data">
          {(event.tools_available as string[])?.length ?? 0} tools available to the planner
        </span>,
      );

    case 'plan': {
      const steps = (event.steps as PlanStep[]) ?? [];
      return shell(
        <ListChecks className="text-cyan h-3 w-3" aria-hidden />,
        <span className="text-ink-0">
          Plan emitted — {steps.length} step{steps.length === 1 ? '' : 's'} chosen from the
          catalogue
        </span>,
        <>
          <div className="mb-1 italic">{String(event.rationale ?? '')}</div>
          <ol className="space-y-0.5">
            {steps.map((step) => (
              <li key={step.id} className="flex gap-1.5">
                <span className="data text-cyan shrink-0">{step.id}.</span>
                <span className="min-w-0">
                  <span className="data text-ink-1">{step.tool}</span>
                  {step.why && <span className="text-ink-3"> — {step.why}</span>}
                </span>
              </li>
            ))}
          </ol>
        </>,
      );
    }

    case 'tool_call':
      return shell(
        <Wrench className="text-ink-2 h-3 w-3" aria-hidden />,
        <>
          calling <span className="data text-ink-0">{String(event.tool)}</span>
        </>,
      );

    case 'tool_result':
      return shell(
        event.ok ? (
          <Check className="text-jade h-3 w-3" aria-hidden />
        ) : (
          <X className="text-red h-3 w-3" aria-hidden />
        ),
        <>
          <span className="data">{String(event.tool)}</span>{' '}
          <span className="text-ink-3">
            {String(event.latency_ms)}ms · {String(event.evidence_count)} evidence
          </span>
        </>,
        event.ok ? String(event.summary) : String(event.error ?? 'failed'),
        event.ok ? 'default' : 'bad',
      );

    case 'ui_spec':
      return shell(
        <ChevronRight className="text-cyan h-3 w-3" aria-hidden />,
        <span className="text-ink-0">Map instructed</span>,
        <span className="data">
          layers: {((event.spec as { layers: string[] })?.layers ?? []).join(', ') || 'none'}
        </span>,
      );

    case 'critic': {
      const verdict = String(event.verdict);
      const bounced = verdict === 'revise';
      return shell(
        bounced ? (
          <Gavel className="text-amber h-3 w-3" aria-hidden />
        ) : verdict === 'escalate' ? (
          <AlertTriangle className="text-amber h-3 w-3" aria-hidden />
        ) : (
          <Gavel className="text-jade h-3 w-3" aria-hidden />
        ),
        <>
          Critic {bounced ? 'REJECTED the draft' : verdict === 'escalate' ? 'escalated' : 'approved'}{' '}
          <span className="text-ink-3">(round {String(event.round)})</span>
        </>,
        String(event.reason ?? ''),
        bounced || verdict === 'escalate' ? 'warn' : 'good',
      );
    }

    case 'step':
      return shell(
        <CircleDashed className="text-ink-3 h-3 w-3" aria-hidden />,
        <>
          <span className="data">{String(event.node)}</span>{' '}
          <span className="text-ink-3">{String(event.detail ?? '')}</span>
        </>,
      );

    case 'decomposition': {
      const parts = (event.parts as { id: number; text: string; intent: string }[]) ?? [];
      if (parts.length < 2) return null;
      return shell(
        <Split className="text-violet h-3 w-3" aria-hidden />,
        <>
          Compound question — split into <span className="data">{parts.length}</span> parts
          <span className="text-ink-3"> ({String(event.method)})</span>
        </>,
        <div className="space-y-0.5">
          {parts.map((part) => (
            <div key={part.id} className="flex gap-1.5">
              <span className="data text-violet shrink-0">{part.intent}</span>
              <span className="min-w-0 flex-1 truncate">{part.text}</span>
            </div>
          ))}
          <div className="text-ink-3">
            tools required: <span className="data">{((event.tools as string[]) ?? []).join(', ')}</span>
          </div>
        </div>,
      );
    }

    case 'error':
      return shell(
        <Ban className="text-red h-3 w-3" aria-hidden />,
        <span className="text-red">Run failed</span>,
        <>
          {String(event.message)}
          {event.detail ? <div className="mt-0.5">{String(event.detail)}</div> : null}
        </>,
        'bad',
      );

    default:
      return null;
  }
}

/** Minimal markdown: bold, bullets, paragraphs. Enough for the answer format we
 *  ask the model for, and no dependency for the rest. */
function Answer({ text }: { text: string }) {
  const blocks = text.trim().split(/\n{2,}/);
  return (
    <div className="space-y-2">
      {blocks.map((block, index) => {
        const lines = block.split('\n');
        const isList = lines.every((l) => stripBullet(l).bullet);
        if (isList) {
          return (
            <ul key={index} className="space-y-1">
              {lines.map((line, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="bg-cyan mt-1.5 h-1 w-1 shrink-0 rounded-full" />
                  <span className="text-ink-1 text-xs leading-relaxed">
                    {inline(stripBullet(line).text)}
                  </span>
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={index} className="text-ink-1 text-xs leading-relaxed">
            {inline(block)}
          </p>
        );
      })}
    </div>
  );
}

export function ChatPanel({
  run,
  onAsk,
  onStop,
  disabled,
  placeLabel,
  language,
  onLanguage,
  speak,
  onSpeak,
  voices,
  onClose,
}: {
  run: AgentRun;
  onAsk: (question: string) => void;
  onStop: () => void;
  disabled: boolean;
  placeLabel: string | null;
  language: string;
  onLanguage: (code: string) => void;
  speak: boolean;
  onSpeak: (on: boolean) => void;
  voices: VoiceOption[];
  onClose: () => void;
}) {
  const [draft, setDraft] = useState('');
  const scroller = useRef<HTMLDivElement>(null);
  const speaker = useSpeaker();

  // Play the advisory as soon as it lands, when speech was asked for. Attempted,
  // not assumed: a browser that blocks autoplay leaves `blocked` set and the
  // panel shows a play button rather than pretending it played.
  const audioUri = run.spoken?.ok ? run.spoken.audio : null;
  useEffect(() => {
    if (audioUri) void speaker.play(audioUri);
    // `speaker` is stable enough for this; re-running on the URI is the intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioUri]);

  // Follow the trace as it grows; a timeline you have to chase is useless.
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  }, [run.events.length, run.final, run.localised, run.spoken]);

  const submit = () => {
    const question = draft.trim();
    if (!question || disabled || run.running) return;
    setDraft('');
    onAsk(question);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
        <MessageSquare className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Ask ORCA</span>
        <div className="ml-auto flex min-w-0 items-center gap-2">
          {run.running && (
            <button
              type="button"
              onClick={onStop}
              className="text-ink-2 hover:text-red flex items-center gap-1 text-2xs transition-colors"
            >
              <Square className="h-2.5 w-2.5" aria-hidden />
              stop
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="text-ink-2 hover:bg-abyss-2/70 hover:text-cyan rounded p-1 transition-colors"
            aria-label="Close Ask ORCA chat"
            title="Close chat to a floating AI button"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      </div>

      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {run.events.length === 0 && !run.final && !run.running && (
          <div className="space-y-3">
            <p className="text-ink-2 text-xs leading-relaxed">
              Ask in plain language. ORCA will choose the right tools, calculate a verified
              safety verdict where needed, and answer about the place you selected on the map.
            </p>
            {!disabled && (
              <div className="space-y-1">
                <div className="label">Try</div>
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => onAsk(suggestion)}
                    className="border-hairline text-ink-1 hover:border-cyan/40 hover:text-cyan block w-full rounded border px-2.5 py-1.5 text-left text-xs transition-colors"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
            {disabled && (
              <p className="text-amber text-xs leading-snug">
                Pick a point on the map first — ORCA answers about a place, not in the abstract.
              </p>
            )}
          </div>
        )}

        {run.question && (
          <div className="mb-3">
            <div className="label mb-1">You asked</div>
            <p className="text-ink-0 text-xs leading-relaxed">{run.question}</p>
            {placeLabel && (
              <p className="text-ink-3 data mt-0.5 text-2xs">about {placeLabel}</p>
            )}
          </div>
        )}

        {run.running && !SHOW_AGENT_TRACE && <WorkingMotion />}

        {/* Detailed plan + agent trace kept in code; hidden for the prototype UI. */}
        {SHOW_AGENT_TRACE && run.plan.length > 0 && (
          <div className="raised mb-3 rounded px-2.5 py-2">
            <div className="label mb-1.5">
              Plan · {run.plan.filter((s) => s.status === 'done').length}/{run.plan.length} complete
            </div>
            <ol className="space-y-1">
              {run.plan.map((step) => (
                <li key={step.id} className="flex items-center gap-2">
                  <StepIcon status={step.status} />
                  <span
                    className={clsx(
                      'data truncate text-2xs',
                      step.status === 'done'
                        ? 'text-ink-1'
                        : step.status === 'running'
                          ? 'text-cyan'
                          : step.status === 'failed'
                            ? 'text-red'
                            : 'text-ink-3',
                    )}
                  >
                    {step.tool}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {SHOW_AGENT_TRACE && run.events.length > 0 && (
          <div className="border-hairline mb-3 border-t pt-2">
            <div className="label mb-2 flex items-center gap-1.5">
              <span>Agent trace</span>
              {run.running && <Loader2 className="text-cyan h-2.5 w-2.5 animate-spin" aria-hidden />}
              <span className="text-ink-3 ml-auto normal-case">
                timings measured in the browser
              </span>
            </div>
            <div>
              {run.events.map((event, index) => (
                <TraceRow key={index} event={event} elapsedMs={run.timings[index]} />
              ))}
            </div>
          </div>
        )}

        {run.final?.answer && (
          <div className="border-hairline border-t pt-3">
            <div className="label mb-1.5">ORCA</div>
            <Answer text={run.final.answer} />
            {SHOW_AGENT_TRACE && run.final.critic?.rounds > 1 && (
              <p className="text-amber mt-2 flex items-start gap-1 text-2xs leading-snug">
                <Gavel className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
                <span>
                  The critic rejected {run.final.critic.rounds - 1} earlier draft
                  {run.final.critic.rounds - 1 === 1 ? '' : 's'} because its claims did not match
                  the verified evidence. This is the version that passed.
                </span>
              </p>
            )}
          </div>
        )}

        <LocalisedAnswer
          localised={run.localised}
          spoken={run.spoken}
          languageName={voices.find((v) => v.code === language)?.name ?? language}
          playing={speaker.playing}
          blocked={speaker.blocked}
          onPlay={(uri) => void speaker.play(uri)}
          onStop={speaker.stop}
        />

        {run.error && (
          <div className="border-red/40 bg-red/8 mt-3 rounded border px-3 py-2">
            <div className="label text-red mb-1">Agent run failed</div>
            <p className="text-ink-1 text-2xs leading-snug">{run.error}</p>
            <p className="text-ink-2 mt-1 text-2xs leading-snug">
              The safety card on the right remains available because it is calculated separately
              from the conversational answer.
            </p>
          </div>
        )}
      </div>

      <div className="border-hairline space-y-1.5 border-t p-2">
        <VoiceBar
          language={language}
          onLanguage={onLanguage}
          speak={speak}
          onSpeak={onSpeak}
          onQuestion={onAsk}
          disabled={disabled || run.running}
          voices={voices}
        />
        <div className="flex items-end gap-1.5">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            placeholder={disabled ? 'Pick a point on the map first' : 'Ask about these waters…'}
            rows={2}
            disabled={disabled}
            className="bg-abyss-0 border-hairline text-ink-0 placeholder:text-ink-3 focus:border-cyan/50 min-h-0 flex-1 resize-none rounded border px-2 py-1.5 text-xs outline-none transition-colors disabled:opacity-50"
          />
          <button
            type="button"
            onClick={submit}
            disabled={disabled || run.running || !draft.trim()}
            className="bg-cyan/15 border-cyan/40 text-cyan hover:bg-cyan/25 rounded border p-1.5 transition-colors disabled:opacity-30"
            aria-label="Send"
          >
            {run.running ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <ArrowUp className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
