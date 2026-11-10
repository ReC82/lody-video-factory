// Tests de composition temporelle (fonctions pures de src/timeline.mjs) — `npm test`.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as T from '../src/timeline.mjs';
import { envelope, synthesize } from '../scripts/make-audio.mjs';

test('format 15 s, 1080x1920, 30 fps = 450 images', () => {
  assert.equal(T.FPS, 30);
  assert.equal(T.DURATION_IN_FRAMES, 450);
  assert.equal(T.DURATION_IN_FRAMES / T.FPS, 15);
  assert.equal(T.WIDTH, 1080);
  assert.equal(T.HEIGHT, 1920);
});

test('keyframes : bornes maintenues, valeurs aux clés et interpolation monotone', () => {
  const keys = [[10, 0], [20, 100]];
  assert.equal(T.keyframes(0, keys), 0);
  assert.equal(T.keyframes(10, keys), 0);
  assert.equal(T.keyframes(20, keys), 100);
  assert.equal(T.keyframes(99, keys), 100);
  assert.equal(T.keyframes(15, keys), 50);
  let prev = -1;
  for (let f = 10; f <= 20; f++) {
    const v = T.keyframes(f, keys);
    assert.ok(v >= prev);
    prev = v;
  }
  assert.throws(() => T.keyframes(0, []));
});

test('les mots et syllabes tiennent dans la vidéo, sans chevauchement', () => {
  let last = -1;
  for (const [a, b] of T.SYLLABLES) {
    assert.ok(a > last && b > a && b <= T.DURATION_IN_FRAMES);
    last = b;
  }
  assert.ok(T.SYLLABLES.length >= 20);
  for (let p = 0; p < T.PHRASES.length; p++) {
    const [a, b] = T.phraseBounds(p);
    assert.ok(a < b && b < T.DURATION_IN_FRAMES);
  }
});

test('clignements : paupière fermée au sommet, ouverte entre deux clignements', () => {
  for (const b of T.BLINKS) {
    assert.equal(T.blink(b + 2), 1);
    assert.equal(T.blink(b - 1), 0);
    assert.ok(T.blink(b + 1) > 0 && T.blink(b + 4) > 0);
  }
  assert.equal(T.blink(60), 0);
});

test('regard : reste borné et change réellement de direction', () => {
  const xs = [];
  for (let f = 0; f < T.DURATION_IN_FRAMES; f++) {
    const g = T.gaze(f);
    assert.ok(Math.abs(g.x) <= 1 && Math.abs(g.y) <= 1);
    xs.push(g.x);
  }
  assert.ok(Math.max(...xs) > 0.7, 'regarde vers le graphique');
  assert.ok(Math.min(...xs) < -0.7, "regarde vers l'oiseau");
});

test('bras droit : salut, désignation puis au revoir', () => {
  assert.ok(T.armRight(60).shoulder < -100);
  assert.ok(T.armRight(200).shoulder < -60);
  assert.ok(T.armRight(130).shoulder > -30);
  assert.ok(T.armRight(430).shoulder < -100);
  // le salut oscille (coude)
  const elbows = new Set(Array.from({ length: 30 }, (_, i) => Math.round(T.armRight(40 + i).elbow)));
  assert.ok(elbows.size > 10);
});

test('décor : nuages en boucle, herbe et oiseaux animés', () => {
  for (let f = 0; f < T.DURATION_IN_FRAMES; f += 7) {
    const x = T.cloudX(f, 2.4, 500);
    assert.ok(x >= -300 && x < T.WIDTH + 300);
  }
  assert.notEqual(T.cloudX(0, 1, 0), T.cloudX(1, 1, 0));
  assert.notEqual(T.grassSway(0, 3), T.grassSway(1, 3));
  assert.notEqual(T.wingFlap(0), T.wingFlap(3));
});

test('graphique et texte : barres croissantes, progression 0→1, titre lettre par lettre', () => {
  assert.equal(T.barHeight(150, 0), 0);
  assert.equal(T.barHeight(300, 3), T.BAR_TARGETS[3]);
  assert.equal(T.progress(0), 0);
  assert.equal(T.progress(T.DURATION_IN_FRAMES - 1), 1);
  assert.equal(T.titleLetters(0, 'abc'), 0);
  assert.equal(T.titleLetters(6, 'abc'), 1);
  assert.equal(T.titleLetters(100, 'abc'), 3);
  assert.equal(T.WORDS[T.currentWordIndex(T.WORDS[0].start)].text, T.WORDS[0].text);
  assert.equal(T.currentWordIndex(5), -1);
});

test('bouche synchronisée : ouverte sur les syllabes, fermée dans les silences', () => {
  const env = envelope(synthesize());
  assert.equal(env.length, T.DURATION_IN_FRAMES);
  for (const [a, b] of T.SYLLABLES) {
    const mid = Math.floor((a + b) / 2);
    assert.ok(T.mouthOpen(mid, env) > 0.25, `syllabe ${a}-${b}`);
  }
  for (const f of [0, 10, 120, 260, 420, 449]) assert.equal(T.mouthOpen(f, env), 0, `silence ${f}`);
});
