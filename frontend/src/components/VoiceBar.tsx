/**
 * Press to talk, and the language the reply comes back in.
 *
 * Design decisions that are not cosmetic:
 *
 * * **The mic meter is driven by the live signal, not by an animation.** A
 *   pulsing ring that pulses whether or not the mic works is a lie, and it is
 *   the exact lie a demo cannot afford: the failure mode we are guarding
 *   against is a muted mic on someone else's laptop.
 * * **The transcript is shown before it is sent.** ASR on a fisherman's
 *   question with a place name in it is the least reliable link in the chain,
 *   so the user sees what ORCA heard and can fix it. Auto-sending a
 *   misheard question and answering it confidently is worse than a slower
 *   round trip.
 * * **Language gaps are stated.** Konkani has no native Bulbul voice, so the
 *   selector says so on the option instead of quietly speaking Marathi at
 *   someone and letting them work it out.
 */

import { useEffect, useState } from 'react';
import { AlertTriangle, Check, Loader2, Mic, Square, Volume2, X } from 'lucide-react';
import { clsx } from 'clsx';
import type { Transcript } from '@/hooks/useVoice';
import { useMicrophone } from '@/hooks/useVoice';

export interface VoiceOption {
  code: string;
  name: string;
  speaker: string;
  native_voice: boolean;
  note: string | null;
  asr_primary: string;
}

/** Shown before /language/voices answers, so the selector is never empty. */
const FALLBACK: VoiceOption[] = [
  { code: 'en', name: 'English', speaker: 'anushka', native_voice: true, note: null, asr_primary: 'groq-whisper' },
  { code: 'ta', name: 'Tamil', speaker: 'vidya', native_voice: true, note: null, asr_primary: 'sarvam-saarika' },
  { code: 'hi', name: 'Hindi', speaker: 'anushka', native_voice: true, note: null, asr_primary: 'sarvam-saarika' },
];

export function useVoiceRoster() {
  const [voices, setVoices] = useState<VoiceOption[]>(FALLBACK);

  useEffect(() => {
    let live = true;
    fetch('/api/language/voices')
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (live && payload?.voices?.length) setVoices(payload.voices as VoiceOption[]);
      })
      .catch(() => {
        /* the fallback list keeps the selector usable offline */
      });
    return () => {
      live = false;
    };
  }, []);

  return voices;
}

export function VoiceBar({
  language,
  onLanguage,
  speak,
  onSpeak,
  onQuestion,
  disabled,
  voices,
}: {
  language: string;
  onLanguage: (code: string) => void;
  speak: boolean;
  onSpeak: (on: boolean) => void;
  /** Called when the user confirms what ORCA heard. */
  onQuestion: (question: string) => void;
  disabled: boolean;
  voices: VoiceOption[];
}) {
  const [heard, setHeard] = useState<Transcript | null>(null);
  const [edited, setEdited] = useState('');

  const mic = useMicrophone((transcript) => {
    setHeard(transcript);
    setEdited(transcript.text);
  });

  const recording = mic.state === 'recording';
  const busy = mic.state === 'transcribing' || mic.state === 'requesting';
  const selected = voices.find((voice) => voice.code === language);

  // The mismatch worth surfacing: the user picked one language and spoke
  // another. Answering the wrong question in the wrong language is the failure
  // this catches, and the server reports the detection on the transcript
  // precisely so the UI can.
  const spokenOther =
    heard?.detected && heard.detected.code !== heard.language && heard.detected.confidence > 0.5
      ? heard.detected
      : null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => (recording ? mic.stop() : void mic.start(language))}
          disabled={disabled || busy}
          className={clsx(
            'relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-colors',
            recording
              ? 'border-red/60 bg-red/20 text-red'
              : 'border-cyan/40 bg-cyan/15 text-cyan hover:bg-cyan/25',
            'disabled:opacity-30',
          )}
          aria-label={recording ? 'Stop recording' : 'Ask by voice'}
          title={recording ? 'Stop and transcribe' : `Ask by voice in ${selected?.name ?? language}`}
        >
          {/* The ring is scaled by the measured input level, so a dead mic
              visibly does nothing. */}
          {recording && (
            <span
              className="border-red/50 pointer-events-none absolute inset-0 rounded-full border"
              style={{
                transform: `scale(${1 + Math.min(mic.level, 1) * 0.55})`,
                opacity: 0.25 + Math.min(mic.level, 1) * 0.6,
                transition: 'transform 80ms linear, opacity 80ms linear',
              }}
            />
          )}
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : recording ? (
            <Square className="h-3 w-3" aria-hidden />
          ) : (
            <Mic className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>

        <select
          value={language}
          onChange={(event) => onLanguage(event.target.value)}
          className="bg-abyss-0 border-hairline text-ink-1 focus:border-cyan/50 min-w-0 flex-1 rounded border px-1.5 py-1 text-2xs outline-none"
          aria-label="Reply language"
        >
          {voices.map((voice) => (
            <option key={voice.code} value={voice.code}>
              {voice.name}
              {voice.native_voice ? '' : ' — no native voice'}
            </option>
          ))}
        </select>

        <button
          type="button"
          onClick={() => onSpeak(!speak)}
          className={clsx(
            'flex shrink-0 items-center gap-1 rounded border px-1.5 py-1 text-2xs transition-colors',
            speak
              ? 'border-jade/40 bg-jade/15 text-jade'
              : 'border-hairline text-ink-3 hover:text-ink-1',
          )}
          aria-pressed={speak}
          title={speak ? 'The advisory will be spoken' : 'Text only'}
        >
          <Volume2 className="h-3 w-3" aria-hidden />
          speak
        </button>
      </div>

      {recording && (
        <p className="text-ink-3 text-2xs">
          Listening — {(mic.elapsedMs / 1000).toFixed(1)}s. Press again when you are done.
        </p>
      )}

      {mic.state === 'error' && mic.error && (
        <div className="border-amber/40 bg-amber/8 flex items-start gap-1.5 rounded border px-2 py-1.5">
          <AlertTriangle className="text-amber mt-px h-3 w-3 shrink-0" aria-hidden />
          <p className="text-ink-1 flex-1 text-2xs leading-snug">{mic.error}</p>
          <button
            type="button"
            onClick={mic.clearError}
            className="text-ink-3 hover:text-ink-1"
            aria-label="Dismiss"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </div>
      )}

      {heard && (
        <div className="border-hairline bg-abyss-0/60 rounded border px-2 py-1.5">
          <div className="mb-1 flex items-center gap-1">
            <span className="label">ORCA heard</span>
            <span className="data text-ink-3 text-2xs">
              {heard.provider}
              {heard.duration_s ? ` · ${heard.duration_s.toFixed(1)}s` : ''}
            </span>
            <button
              type="button"
              onClick={() => setHeard(null)}
              className="text-ink-3 hover:text-ink-1 ml-auto"
              aria-label="Discard"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          </div>
          <textarea
            value={edited}
            onChange={(event) => setEdited(event.target.value)}
            rows={2}
            className="bg-abyss-0 border-hairline text-ink-0 focus:border-cyan/50 w-full resize-none rounded border px-1.5 py-1 text-xs outline-none"
          />
          {spokenOther && (
            <p className="text-amber mt-1 text-2xs leading-snug">
              That sounds like {spokenOther.name}
              {spokenOther.romanised ? ' (romanised)' : ''}, not {selected?.name ?? heard.language}.
              Switch the language if the transcript looks wrong.
            </p>
          )}
          <button
            type="button"
            onClick={() => {
              const question = edited.trim();
              if (!question) return;
              setHeard(null);
              onQuestion(question);
            }}
            disabled={!edited.trim() || disabled}
            className="border-cyan/40 bg-cyan/15 text-cyan hover:bg-cyan/25 mt-1.5 flex items-center gap-1 rounded border px-2 py-1 text-2xs transition-colors disabled:opacity-30"
          >
            <Check className="h-3 w-3" aria-hidden />
            Ask this
          </button>
        </div>
      )}
    </div>
  );
}
