// Tests des fonctions pures du moteur de scène (#150) : `node --test test/*.test.mjs` (aucun navigateur).
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {
  autoBlink, cameraState, ease, EASINGS, gestureLocal, mergePoses, mouthAt, objectState, puppetPose, sample,
  transitionState, visemesOf,
} from '../lib/anim.mjs';
import {decodeWav, envelope} from '../lib/wav.mjs';

const RIG = JSON.parse(readFileSync(new URL('../../../resource/animation-assets/voltix/v1/voltix-rig.json',
  import.meta.url), 'utf8'));
const CANVAS = {fps: 30, width: 1080, height: 1920};

test('toutes les courbes du contrat partent de 0 et arrivent à 1', () => {
  for (const name of EASINGS) {
    assert.equal(ease(name, 0), 0, name);
    assert.equal(ease(name, 1), 1, name);
  }
  assert.ok(Math.max(...Array.from({length: 50}, (_, i) => ease('spring', i / 50))) > 1, 'le ressort dépasse');
});

test('images-clés : maintien aux bornes, interpolation entre deux clés', () => {
  const keys = [{frame: 10, x: 0}, {frame: 20, x: 100}];
  assert.equal(sample(keys, 0, 'x'), 0);
  assert.equal(sample(keys, 30, 'x'), 100);
  assert.equal(sample(keys, 15, 'x', 'linear'), 50);
  assert.equal(sample([], 5, 'x', 'linear', 7), 7);
  const state = objectState({position: [{frame: 0, x: 1, y: 2}]}, 3);
  assert.deepEqual(state, {x: 1, y: 2, scale: 1, rotation: 0, opacity: 1});
});

test('caméra par défaut centrée ; transitions fondu et glissement', () => {
  assert.deepEqual(cameraState(undefined, 0, CANVAS), {x: 540, y: 960, zoom: 1, rotation: 0});
  const render = {transition_in: {type: 'slide_left', frames: 10}, transition_out: {type: 'fade', frames: 10}};
  assert.equal(transitionState(render, 0, 100, CANVAS).dx, 1080);
  assert.equal(transitionState(render, 10, 100, CANVAS).dx, 0);
  assert.equal(transitionState(render, 50, 100, CANVAS).opacity, 1);
  assert.equal(transitionState(render, 99, 100, CANVAS).opacity, 0);
});

test('fusion des poses : maintien des clés absentes, clé inconnue refusée', () => {
  const pose = mergePoses({rotate: {a: 1}, lift: 2}, {rotate: {b: 3}});
  assert.deepEqual(pose, {rotate: {a: 1, b: 3}, lift: 2});
  assert.throws(() => mergePoses({script: 'x'}), /inconnue/);
});

test('geste « saluer » : bras levé au milieu, retour au repos après la fenêtre', () => {
  const obj = {gestures: [{start: 0, end: 30, name: 'saluer'}]};
  assert.equal(puppetPose(RIG, obj, 0).rotate['arm-left'] ?? 0, 0);
  assert.ok(puppetPose(RIG, obj, 11).rotate['arm-left'] < -100, 'bras levé');
  const mid = puppetPose(RIG, obj, 3).rotate['arm-left'];
  assert.ok(mid < 0 && mid > -128, 'interpolé, pas de saut');
  assert.equal(puppetPose(RIG, obj, 40).rotate['arm-left'] ?? 0, 0);
});

test('geste tenu au-delà de sa durée, compressé dans une fenêtre plus courte', () => {
  const spec = RIG.gestures.montrer;
  assert.equal(gestureLocal(spec, {start: 0, end: 100}, 80), spec.frames);
  assert.equal(gestureLocal(spec, {start: 0, end: 12}, 6), 12);
  const obj = {gestures: [{start: 0, end: 100, name: 'montrer'}]};
  assert.equal(puppetPose(RIG, obj, 90).rotate['arm-right'], 118);
  assert.equal(puppetPose(RIG, obj, 90).select['hand-right'], 'index');
});

test('rebond : compression puis envol des ressorts', () => {
  const obj = {gestures: [{start: 0, end: 28, name: 'rebond'}]};
  assert.ok(puppetPose(RIG, obj, 6).lift < -40);
  assert.ok(puppetPose(RIG, obj, 15).air > 70);
  assert.equal(puppetPose(RIG, obj, 40).air, 0);
});

test('expressions dans le temps et clignement automatique', () => {
  const obj = {expressions: [{frame: 0, name: 'sourire'}, {frame: 50, name: 'surprise'}]};
  assert.equal(puppetPose(RIG, obj, 10).select.eyebrows, 'joie');
  assert.equal(puppetPose(RIG, obj, 60).select.eyebrows, 'surprise');
  const blinks = Array.from({length: 300}, (_, f) => autoBlink(f)).filter((v) => v === 'fermes').length;
  assert.ok(blinks >= 4 && blinks <= 8);
});

test('visèmes français et bouche pilotée par l’enveloppe de la voix', () => {
  assert.deepEqual(visemesOf('bonjour'), ['mbp', 'o', 'ou', 'i']);
  assert.ok(visemesOf('Voltix').includes('fv'));
  const cues = [{text: 'Salut', start: 10, end: 20}];
  assert.equal(mouthAt(cues, null, 5), null, 'hors réplique : bouche de l’expression');
  const env = Array.from({length: 30}, (_, f) => (f >= 12 && f < 18 ? 0.8 : 0));
  assert.equal(mouthAt(cues, env, 10), 'mbp', 'silence dans la réplique : lèvres fermées');
  assert.notEqual(mouthAt(cues, env, 14), 'mbp');
  const obj = {lipsync: true, expressions: [{frame: 0, name: 'sourire'}]};
  assert.equal(puppetPose(RIG, obj, 5, {cues, envelope: env}).select['mouth-visemes'], 'sourire');
  assert.notEqual(puppetPose(RIG, obj, 14, {cues, envelope: env}).select['mouth-visemes'], 'sourire');
});

function wav(samples, rate = 8000) {
  const data = Buffer.alloc(44 + samples.length * 2);
  data.write('RIFF', 0);
  data.writeUInt32LE(36 + samples.length * 2, 4);
  data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24);
  data.writeUInt32LE(rate * 2, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write('data', 36);
  data.writeUInt32LE(samples.length * 2, 40);
  samples.forEach((s, i) => data.writeInt16LE(Math.round(s * 32767), 44 + i * 2));
  return data;
}

test('enveloppe par image : silence à 0, parole proche de 1, fenêtre décalée', () => {
  const rate = 8000;
  const samples = Array.from({length: rate * 2}, (_, i) => (i >= rate ? 0.5 * Math.sin(i / 3) : 0));
  const decoded = decodeWav(wav(samples, rate));
  assert.equal(decoded.sampleRate, rate);
  const env = envelope(decoded, 10);
  assert.equal(env.length, 20);
  assert.equal(env[2], 0);
  assert.ok(env[15] > 0.9);
  assert.deepEqual(envelope(decoded, 10, 15, 3), env.slice(15, 18));
  assert.throws(() => decodeWav(Buffer.from('pas un wav')), /WAV/);
});

test('échelle de rendu (#121) : prévisualisation bornée, rendu final par défaut', async () => {
  const {renderScale} = await import('../lib/input.mjs');
  assert.equal(renderScale({}), 1);
  assert.equal(renderScale({render: {scale: 0.5}}), 0.5);
  assert.equal(renderScale({render: {scale: 0.1}}), 1);
  assert.equal(renderScale({render: {scale: 2}}), 1);
  assert.equal(renderScale({render: {scale: 'x'}}), 1);
});
