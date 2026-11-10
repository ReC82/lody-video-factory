// Preuve de synchronisation labiale : mesure dans la VIDÉO rendue l'aire sombre de la bouche
// (intérieur #5a1d2b) image par image et la corrèle avec l'enveloppe RMS de l'audio factice.
// Usage : node scripts/lipsync-proof.mjs out/prototype-118.mp4
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const input = process.argv[2] ?? join(root, 'out', 'prototype-118.mp4');
const [w, h, x, y] = [200, 150, 440, 1040];
const raw = execFileSync('ffmpeg', ['-v', 'error', '-i', input, '-vf', `crop=${w}:${h}:${x}:${y},format=gray`, '-f', 'rawvideo', '-'], { maxBuffer: 1 << 30 });
const size = w * h;
const area = [];
for (let f = 0; f * size < raw.length; f++) {
  let dark = 0;
  for (let i = f * size; i < (f + 1) * size; i++) if (raw[i] < 70) dark++;
  area.push(dark);
}
const env = JSON.parse(readFileSync(join(root, 'src', 'envelope.json'), 'utf8'));
const n = Math.min(area.length, env.length);
const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
const corr = (a, b) => {
  const ma = mean(a), mb = mean(b);
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < a.length; i++) { num += (a[i] - ma) * (b[i] - mb); da += (a[i] - ma) ** 2; db += (b[i] - mb) ** 2; }
  return num / Math.sqrt(da * db);
};
const A = area.slice(0, n), E = env.slice(0, n);
const speaking = A.filter((_, i) => E[i] > 0.3), silent = A.filter((_, i) => E[i] < 0.02);
console.log(`images analysées : ${n}`);
console.log(`corrélation aire de bouche ↔ enveloppe audio : ${corr(A, E).toFixed(3)}`);
for (const lag of [-2, -1, 1, 2]) {
  const a = lag > 0 ? A.slice(lag) : A.slice(0, n + lag), e = lag > 0 ? E.slice(0, n - lag) : E.slice(-lag);
  console.log(`  décalage ${lag > 0 ? '+' : ''}${lag} image(s) : ${corr(a, e).toFixed(3)}`);
}
console.log(`aire moyenne (px) pendant la parole : ${mean(speaking).toFixed(0)} — en silence : ${mean(silent).toFixed(0)}`);
