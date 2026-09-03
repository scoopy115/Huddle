/**
 * Subtle interface sounds, synthesised with the Web Audio API (no assets, no network).
 * Everything is short, quiet and soft-edged: sine/triangle tones with a 5 ms attack and an
 * exponential release, so they read as "friendly taps", not beeps. Disabled with the
 * "Interface sounds" setting; the AudioContext is created lazily on the first user gesture.
 */
let ctx: AudioContext | null = null;
let enabled = true;
let lastTap = 0;

export function setSoundsEnabled(v: boolean) { enabled = v; }

function context(): AudioContext | null {
  if (!enabled) return null;
  try {
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx;
  } catch { return null; }
}

interface ToneOpts { dur?: number; type?: OscillatorType; gain?: number; at?: number; glide?: number }

function tone(freq: number, { dur = 0.08, type = "sine", gain = 0.08, at = 0, glide }: ToneOpts = {}) {
  const c = context();
  if (!c) return;
  const t0 = c.currentTime + at;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  if (glide) osc.frequency.exponentialRampToValueAtTime(glide, t0 + dur);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(gain, t0 + 0.005);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(g).connect(c.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

export const sounds = {
  /** Button press: a tiny wooden tick. Rate-limited so rapid clicks don't chatter. */
  tap() {
    const now = performance.now();
    if (now - lastTap < 60) return;
    lastTap = now;
    tone(1400, { dur: 0.035, type: "triangle", gain: 0.035, glide: 900 });
  },
  /** Moving between pages. */
  nav() { tone(880, { dur: 0.05, gain: 0.03, glide: 1100 }); },
  toggleOn() { tone(660, { dur: 0.06, gain: 0.05 }); tone(990, { dur: 0.09, gain: 0.05, at: 0.045 }); },
  toggleOff() { tone(990, { dur: 0.06, gain: 0.05 }); tone(660, { dur: 0.09, gain: 0.05, at: 0.045 }); },
  /** Recording starts: two rising notes. */
  recordStart() { tone(523, { dur: 0.11, gain: 0.07 }); tone(784, { dur: 0.16, gain: 0.07, at: 0.1 }); },
  /** Recording stops: the same two notes, falling. */
  recordStop() { tone(784, { dur: 0.11, gain: 0.07 }); tone(523, { dur: 0.18, gain: 0.07, at: 0.1 }); },
  /** A meeting finished processing: a small three-note chime. */
  success() { tone(659, { dur: 0.11, gain: 0.06 }); tone(880, { dur: 0.11, gain: 0.06, at: 0.1 }); tone(1319, { dur: 0.24, gain: 0.06, at: 0.2 }); },
  /** Something went wrong: a low, soft double thud. */
  error() { tone(220, { dur: 0.12, type: "triangle", gain: 0.06 }); tone(196, { dur: 0.16, type: "triangle", gain: 0.06, at: 0.13 }); },
  /** Palette or dialog opens. */
  open() { tone(740, { dur: 0.05, gain: 0.035, glide: 880 }); },
};
