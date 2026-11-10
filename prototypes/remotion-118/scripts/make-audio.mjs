// Génère l'audio factice (aucun fournisseur payant) : babillage voisé synthétisé à partir des
// syllabes de src/timeline.mjs, plus un fond d'ambiance discret. Sorties :
//   public/voice.wav      — WAV PCM 16 bits mono 48 kHz, 15 s
//   src/envelope.json     — enveloppe RMS normalisée par image (pilote la bouche)
// Entièrement déterministe (PRNG à graine fixe) : deux exécutions produisent des octets identiques.
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { DURATION_IN_FRAMES, FPS, SAMPLE_RATE, SYLLABLES, syllablePitch } from '../src/timeline.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

export function synthesize() {
  const n = Math.round((DURATION_IN_FRAMES / FPS) * SAMPLE_RATE);
  const out = new Float32Array(n);
  let seed = 118;
  const rand = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296) * 2 - 1;
  // fond : bruit rose très doux (vent)
  let b = 0;
  for (let i = 0; i < n; i++) {
    b = 0.995 * b + 0.05 * rand();
    out[i] = 0.02 * b;
  }
  // syllabes : fondamentale + harmoniques modulées par deux formants de voyelle
  const vowels = [[700, 1200], [400, 2000], [300, 2300], [500, 900], [600, 1700]];
  SYLLABLES.forEach(([f0, f1], k) => {
    const s0 = Math.round((f0 / FPS) * SAMPLE_RATE);
    const s1 = Math.round((f1 / FPS) * SAMPLE_RATE);
    const len = s1 - s0;
    const pitch = syllablePitch(k);
    const [fa, fb] = vowels[k % vowels.length];
    for (let i = 0; i < len && s0 + i < n; i++) {
      const t = i / SAMPLE_RATE;
      const env = Math.sin(Math.PI * (i / len)) ** 0.6; // attaque/chute douces
      const vib = 1 + 0.01 * Math.sin(2 * Math.PI * 5 * t);
      let v = 0;
      for (let h = 1; h <= 12; h++) {
        const fh = pitch * h * vib;
        const formant = Math.exp(-(((fh - fa) / 180) ** 2)) + 0.6 * Math.exp(-(((fh - fb) / 250) ** 2)) + 0.08;
        v += (formant / h) * Math.sin(2 * Math.PI * fh * t);
      }
      out[s0 + i] += 0.35 * env * v;
    }
  });
  for (let i = 0; i < n; i++) out[i] = Math.max(-1, Math.min(1, out[i]));
  return out;
}

/** RMS par image, normalisé sur le maximum (0..1). */
export function envelope(samples) {
  const per = SAMPLE_RATE / FPS;
  const rms = [];
  for (let f = 0; f < DURATION_IN_FRAMES; f++) {
    let s = 0;
    const a = Math.round(f * per);
    const z = Math.round((f + 1) * per);
    for (let i = a; i < z; i++) s += samples[i] * samples[i];
    rms.push(Math.sqrt(s / (z - a)));
  }
  const max = Math.max(...rms);
  return rms.map((v) => Math.round((v / max) * 1000) / 1000);
}

export function wav(samples) {
  const buf = Buffer.alloc(44 + samples.length * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + samples.length * 2, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(SAMPLE_RATE, 24); buf.writeUInt32LE(SAMPLE_RATE * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(samples.length * 2, 40);
  samples.forEach((v, i) => buf.writeInt16LE(Math.round(v * 32767), 44 + i * 2));
  return buf;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const samples = synthesize();
  writeFileSync(join(root, 'public', 'voice.wav'), wav(samples));
  writeFileSync(join(root, 'src', 'envelope.json'), JSON.stringify(envelope(samples)) + '\n');
  console.log(`voice.wav : ${samples.length} échantillons ; envelope.json : ${DURATION_IN_FRAMES} images`);
}
