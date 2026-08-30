/**
 * The answer in the user's language, and the audio, with the guard's result
 * shown rather than assumed.
 *
 * This component exists to make one thing visible: **a translated advisory is
 * only trustworthy if the figures survived the translation.** The backend masks
 * every numeral and every verdict word, translates, re-injects them verbatim
 * and then verifies the multiset — after Sarvam rendered "NO-GO" into Tamil as
 * "punishment for those who go astray", which is a verdict inverted by a
 * translator and the worst failure this system can have.
 *
 * So the panel reports two separate facts, because they are separate questions:
 *
 * * **figures intact** — every number and verdict term is present. This is the
 *   safety question, and it is what gates whether the audio is produced at all.
 * * **fully translated** — every clause actually got translated. When this is
 *   false but the first is true, some clause stayed in English on purpose: an
 *   unverified clause is left untranslated rather than translated unsafely.
 *
 * The spoken text is shown too, because it is a summary rather than the whole
 * answer, and a user who hears less than they read should be told why instead
 * of concluding the audio is broken.
 */

import { Languages, Pause, Play, ShieldAlert, ShieldCheck, Volume2, VolumeX } from 'lucide-react';
import { clsx } from 'clsx';
import type { Localised, Spoken } from '@/hooks/useAgentStream';
import { inline, stripBullet } from '@/lib/markdown';

export function LocalisedAnswer({
  localised,
  spoken,
  languageName,
  playing,
  blocked,
  onPlay,
  onStop,
}: {
  localised: Localised | null;
  spoken: Spoken | null;
  languageName: string;
  playing: boolean;
  blocked: boolean;
  onPlay: (dataUri: string) => void;
  onStop: () => void;
}) {
  if (!localised && !spoken) return null;

  const guard = localised?.guard ?? {};
  const numerals = Array.isArray(guard.source_numbers) ? guard.source_numbers.length : null;
  const unitsFailed = typeof guard.units_failed === 'number' ? guard.units_failed : 0;
  const units = typeof guard.units === 'number' ? guard.units : 0;

  return (
    <div className="border-hairline mt-3 border-t pt-3">
      {localised && (
        <>
          <div className="mb-1.5 flex items-center gap-1.5">
            <Languages className="text-cyan h-3 w-3" aria-hidden />
            <span className="label">{languageName}</span>
            <span
              className={clsx(
                'flex items-center gap-1 rounded px-1 py-px text-2xs',
                localised.ok ? 'bg-jade/12 text-jade' : 'bg-red/12 text-red',
              )}
              title={
                localised.ok
                  ? 'Every numeral and verdict term was verified present in the translation.'
                  : 'The guard could not confirm the figures survived. The English answer above stands.'
              }
            >
              {localised.ok ? (
                <ShieldCheck className="h-2.5 w-2.5" aria-hidden />
              ) : (
                <ShieldAlert className="h-2.5 w-2.5" aria-hidden />
              )}
              {localised.ok ? 'figures intact' : 'figures unverified'}
              {numerals ? ` · ${numerals}` : ''}
            </span>
            <span className="data text-ink-3 ml-auto text-2xs">via {localised.provider}</span>
          </div>

          {/* Indic scripts need the line height; at the tracking used for Latin
              body copy the vowel marks collide. */}
          <div className="text-ink-1 space-y-1.5 text-xs" style={{ lineHeight: 1.85 }}>
            {localised.text
              .split('\n')
              .filter((line) => line.trim())
              .map((line, index) => {
                const { text, bullet } = stripBullet(line);
                return (
                  <p key={index} className={bullet ? 'flex gap-1.5' : undefined}>
                    {bullet && <span className="text-cyan shrink-0">·</span>}
                    <span>{inline(text)}</span>
                  </p>
                );
              })}
          </div>

          {!localised.fullyTranslated && (
            <p className="text-amber mt-1.5 text-2xs leading-snug">
              {unitsFailed} of {units} clauses stayed in English: the guard could not verify them,
              and an unverified clause is left untranslated rather than translated unsafely. Every
              figure shown is still correct.
            </p>
          )}
        </>
      )}

      {spoken && (
        <div className="border-hairline mt-2 flex items-start gap-2 rounded border px-2 py-1.5">
          {spoken.ok && spoken.audio ? (
            <>
              <button
                type="button"
                onClick={() => (playing ? onStop() : onPlay(spoken.audio as string))}
                className="border-jade/40 bg-jade/15 text-jade hover:bg-jade/25 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border transition-colors"
                aria-label={playing ? 'Pause' : 'Play the spoken advisory'}
              >
                {playing ? (
                  <Pause className="h-3 w-3" aria-hidden />
                ) : (
                  <Play className="ml-px h-3 w-3" aria-hidden />
                )}
              </button>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <Volume2 className="text-ink-3 h-3 w-3" aria-hidden />
                  <span className="data text-ink-2 text-2xs">
                    {spoken.speaker}
                    {spoken.durationS ? ` · ${spoken.durationS.toFixed(1)}s` : ''}
                  </span>
                  {!spoken.nativeVoice && (
                    <span
                      className="bg-amber/12 text-amber rounded px-1 text-2xs"
                      title={spoken.voiceNote ?? undefined}
                    >
                      substitute voice
                    </span>
                  )}
                  {blocked && (
                    <span className="text-ink-3 text-2xs">
                      the browser blocked autoplay — press play
                    </span>
                  )}
                </div>
                {spoken.summarised && spoken.spokenText && (
                  <p className="text-ink-3 mt-1 text-2xs leading-snug">
                    Spoken: “{spoken.spokenText.slice(0, 160)}
                    {spoken.spokenText.length > 160 ? '…' : ''}” — the verdict and its reason. The
                    detail above is not read aloud.
                  </p>
                )}
                {spoken.voiceNote && (
                  <p className="text-ink-3 mt-0.5 text-2xs leading-snug">{spoken.voiceNote}</p>
                )}
              </div>
            </>
          ) : (
            <>
              <VolumeX className="text-amber mt-px h-3.5 w-3.5 shrink-0" aria-hidden />
              <p className="text-ink-1 flex-1 text-2xs leading-snug">
                {spoken.detail ?? 'The advisory was not spoken.'}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
