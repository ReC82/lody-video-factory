// Reproductibilité de l'audio factice et cohérence avec l'enveloppe commitée à la volée.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { envelope, synthesize, wav } from '../scripts/make-audio.mjs';
import { DURATION_IN_FRAMES, FPS, SAMPLE_RATE } from '../src/timeline.mjs';

const sha = (b) => createHash('sha256').update(b).digest('hex');

test('le WAV factice est déterministe et dure exactement 15 s', () => {
  const a = wav(synthesize());
  const b = wav(synthesize());
  assert.equal(sha(a), sha(b));
  assert.equal(a.toString('ascii', 0, 4), 'RIFF');
  assert.equal(a.readUInt32LE(24), SAMPLE_RATE);
  assert.equal((a.length - 44) / 2, (DURATION_IN_FRAMES / FPS) * SAMPLE_RATE);
});

test("l'enveloppe est normalisée 0..1", () => {
  const env = envelope(synthesize());
  assert.equal(Math.max(...env), 1);
  assert.ok(Math.min(...env) >= 0);
});
