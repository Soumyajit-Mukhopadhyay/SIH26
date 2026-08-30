/**
 * Browser-side WAV encoding, because the ASR providers will not take what
 * `MediaRecorder` produces.
 *
 * `MediaRecorder` gives you `audio/webm;codecs=opus` in Chrome and
 * `audio/mp4` in Safari — neither is a container Sarvam's Saarika endpoint
 * accepts, and ORCA sends Indic audio to Saarika first on measured accuracy
 * grounds (it transcribed a Tamil clip exactly, numerals included, where
 * Whisper garbled the same clip). Sending webm and letting the server relabel
 * it `audio/wav` produces a 400 from the provider that reads like an auth
 * failure, so the conversion happens here, once, in the one place that has a
 * decoder for every format the platform might have recorded in.
 *
 * 16 kHz mono 16-bit is what both ASR models want. Anything richer is uploaded
 * bandwidth that the model discards, and on a harbour phone connection the
 * upload is the slow part of the round trip.
 */

/** What both Saarika and Whisper are trained on. Higher is wasted upload. */
export const ASR_SAMPLE_RATE = 16_000;

/**
 * Decode whatever the platform recorded, then re-encode as 16 kHz mono WAV.
 *
 * Uses `OfflineAudioContext` for the resample rather than dropping samples:
 * naive decimation aliases, and aliasing lands as phantom high-frequency
 * energy that degrades exactly the consonant discrimination ASR depends on.
 */
export async function toAsrWav(blob: Blob): Promise<Blob> {
  const bytes = await blob.arrayBuffer();

  // A plain AudioContext is needed first: OfflineAudioContext cannot decode at
  // a sample rate it was not constructed with, and we do not know the
  // recording's rate until it is decoded.
  const decoder = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await decoder.decodeAudioData(bytes);
  } finally {
    void decoder.close();
  }

  const frames = Math.max(1, Math.round((decoded.duration * ASR_SAMPLE_RATE) | 0));
  const offline = new OfflineAudioContext(1, frames, ASR_SAMPLE_RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const mono = await offline.startRendering();

  return encodeWav(mono.getChannelData(0), ASR_SAMPLE_RATE);
}

/** Float samples in [-1, 1] to a 16-bit PCM WAV blob. */
export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };

  ascii(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM, uncompressed
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, 'data');
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    // Clamp before scaling: a sample above 1.0 wraps to full-scale negative
    // otherwise, which is an audible click and a real risk with mic AGC.
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

/**
 * Peak level over a window of samples, for the mic meter.
 *
 * Peak rather than RMS on purpose: the meter's job is to tell someone their mic
 * is live, and RMS on speech with normal pauses sits near zero often enough to
 * look broken.
 */
export function peakLevel(samples: Float32Array): number {
  let peak = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.abs(samples[i]);
    if (value > peak) peak = value;
  }
  return peak;
}
