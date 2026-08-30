/**
 * The microphone, and the speaker.
 *
 * Two things live here because they share one hard constraint: browsers refuse
 * both `getUserMedia` and audio playback outside a user gesture, and the
 * failures are silent. So every entry point is called from a click, and every
 * refusal is turned into a message a human can act on ("permission denied" is
 * a different problem from "no microphone" is a different problem from "this
 * page is not on https").
 *
 * The recorder does NOT stream to the server. A press-to-talk clip is one or
 * two seconds of speech, and a single upload of a complete recording is both
 * simpler and more accurate than incremental ASR over partial audio — a partial
 * clip cut mid-numeral is exactly the input that produces a wrong figure.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { peakLevel, toAsrWav } from '@/lib/wav';

export interface Transcript {
  text: string;
  language: string;
  provider: string;
  duration_s: number | null;
  fallbacks: [string, string][];
  detected: { code: string; name: string; confidence: number; romanised: boolean } | null;
  bytes: number;
}

export type MicState = 'idle' | 'requesting' | 'recording' | 'transcribing' | 'error';

/** Longer than any press-to-talk question, short enough to bound the upload. */
const MAX_RECORDING_MS = 30_000;

/** Below this, the clip is silence and asking the provider wastes a round trip. */
const SILENCE_PEAK = 0.02;

function micErrorMessage(cause: unknown): string {
  const error = cause as { name?: string; message?: string };
  if (!window.isSecureContext) {
    return 'The microphone needs https (or localhost). This page is not on a secure origin.';
  }
  switch (error?.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Microphone permission was denied. Allow it in the browser’s site settings and try again.';
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No microphone was found on this device.';
    case 'NotReadableError':
      return 'The microphone is in use by another application.';
    default:
      return error?.message || 'The microphone could not be opened.';
  }
}

export function useMicrophone(onTranscript: (transcript: Transcript) => void) {
  const [state, setState] = useState<MicState>('idle');
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const contextRef = useRef<AudioContext | null>(null);
  const frameRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const languageRef = useRef('en');
  const peakRef = useRef(0);

  const teardown = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    frameRef.current = null;
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void contextRef.current?.close();
    contextRef.current = null;
    recorderRef.current = null;
    setLevel(0);
  }, []);

  // A recording left running when the panel unmounts keeps the browser's
  // recording indicator lit, which reads as the app spying on you.
  useEffect(() => teardown, [teardown]);

  const stop = useCallback(() => {
    // `stop()` triggers `onstop`, which is where the upload happens. Tearing the
    // stream down here instead would deliver an empty clip.
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
  }, []);

  const start = useCallback(
    async (language: string) => {
      if (state === 'recording' || state === 'requesting') return;
      setError(null);
      setState('requesting');
      languageRef.current = language;
      peakRef.current = 0;

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            // Harbour-side audio: the browser's own cleanup is better than
            // nothing and costs us nothing.
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
      } catch (cause) {
        setError(micErrorMessage(cause));
        setState('error');
        return;
      }

      streamRef.current = stream;
      chunksRef.current = [];

      // The meter and the silence check read the live signal. Doing this on the
      // recorded blob instead would mean discovering the mic was muted only
      // after the upload.
      const context = new AudioContext();
      contextRef.current = context;
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      context.createMediaStreamSource(stream).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);

      const tick = () => {
        analyser.getFloatTimeDomainData(samples);
        const peak = peakLevel(samples);
        if (peak > peakRef.current) peakRef.current = peak;
        // Smoothed for the eye, but the peak we keep for the silence test is
        // the raw one.
        setLevel((previous) => Math.max(peak, previous * 0.86));
        frameRef.current = requestAnimationFrame(tick);
      };
      frameRef.current = requestAnimationFrame(tick);

      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };

      recorder.onstop = async () => {
        const spokePeak = peakRef.current;
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        teardown();

        if (blob.size === 0 || spokePeak < SILENCE_PEAK) {
          setError('Nothing was recorded — the microphone picked up silence.');
          setState('error');
          return;
        }

        setState('transcribing');
        try {
          const wav = await toAsrWav(blob);
          const form = new FormData();
          form.append('file', wav, 'question.wav');
          form.append('language', languageRef.current);

          const response = await fetch('/api/language/listen', { method: 'POST', body: form });
          if (!response.ok) {
            const detail = await response.text();
            throw new Error(`transcription failed (HTTP ${response.status}): ${detail.slice(0, 180)}`);
          }
          const transcript = (await response.json()) as Transcript;
          if (!transcript.text.trim()) {
            setError('The recording was transcribed as empty. Try again, a little closer to the mic.');
            setState('error');
            return;
          }
          setState('idle');
          onTranscript(transcript);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : 'The recording could not be transcribed.');
          setState('error');
        }
      };

      recorder.start();
      startedAtRef.current = performance.now();
      setElapsedMs(0);
      setState('recording');
      timerRef.current = window.setInterval(() => {
        const elapsed = performance.now() - startedAtRef.current;
        setElapsedMs(elapsed);
        if (elapsed > MAX_RECORDING_MS) stop();
      }, 100);
    },
    [onTranscript, state, stop, teardown],
  );

  return { state, level, error, elapsedMs, start, stop, clearError: () => setError(null) };
}

/**
 * Playback for the advisory audio the agent stream delivers as a data URI.
 *
 * Autoplay is attempted and allowed to fail: a browser that blocks it leaves
 * `blocked` set, and the UI shows a play button instead of pretending the audio
 * played. Silently swallowing the rejection is how you end up with a demo where
 * the judge never hears the Tamil.
 */
export function useSpeaker() {
  const [playing, setPlaying] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const elementRef = useRef<HTMLAudioElement | null>(null);
  const sourceRef = useRef<string | null>(null);

  useEffect(
    () => () => {
      elementRef.current?.pause();
      elementRef.current = null;
    },
    [],
  );

  const play = useCallback(async (dataUri: string) => {
    // One element, reused. A new element per utterance leaves the previous one
    // playing and you get two languages at once.
    if (!elementRef.current) {
      elementRef.current = new Audio();
      elementRef.current.onended = () => setPlaying(false);
      elementRef.current.onpause = () => setPlaying(false);
    }
    const audio = elementRef.current;
    if (sourceRef.current !== dataUri) {
      audio.src = dataUri;
      sourceRef.current = dataUri;
    }
    audio.currentTime = 0;
    try {
      await audio.play();
      setBlocked(false);
      setPlaying(true);
    } catch {
      setBlocked(true);
      setPlaying(false);
    }
  }, []);

  const stop = useCallback(() => {
    elementRef.current?.pause();
    setPlaying(false);
  }, []);

  return { play, stop, playing, blocked };
}
