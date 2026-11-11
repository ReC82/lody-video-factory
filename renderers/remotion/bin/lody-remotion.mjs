#!/usr/bin/env node
// Moteur de scène 2D de Lody (#150), appelé par le worker #120 via LODY_ANIMATION_RENDERER :
//
//   lody-remotion --input scenes/NN-input.json --output scenes/NN.render.mp4
//   lody-remotion --input scenes/NN-input.json --still <image> --output scenes/NN-still.png
//
// Rendu sobre pour un hôte partagé : une page Chrome à la fois (concurrence 1), bundle webpack construit une
// seule fois et réutilisé (`.bundle/<empreinte des sources>`), H.264 yuv420p sans audio (le worker assemble
// et mixe la voix et la musique). Code de sortie 0 = fichier produit ; 2 = arguments ; 1 = échec du rendu.

import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildProps, InputError} from '../lib/input.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const COMPOSITION = 'LodyScene';

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    const value = argv[i + 1];
    if (!['--input', '--output', '--still'].includes(key) || value === undefined) return null;
    args[key.slice(2)] = value;
  }
  if (!args.input || !args.output) return null;
  if (args.still !== undefined && !/^\d+$/.test(args.still)) return null;
  return args;
}

function sourceDigest() {
  const hash = createHash('sha256');
  const walk = (dir) => {
    for (const name of readdirSync(dir).sort()) {
      const file = path.join(dir, name);
      if (statSync(file).isDirectory()) walk(file);
      else hash.update(name).update(readFileSync(file));
    }
  };
  walk(path.join(ROOT, 'src'));
  walk(path.join(ROOT, 'lib'));
  hash.update(readFileSync(path.join(ROOT, 'package-lock.json')));
  return hash.digest('hex').slice(0, 16);
}

/** Bundle webpack réutilisé tant que les sources ne changent pas (évite le pic mémoire de l'empaquetage). */
async function ensureBundle() {
  const target = path.join(ROOT, '.bundle', sourceDigest());
  if (existsSync(path.join(target, 'index.html'))) return target;
  const {bundle} = await import('@remotion/bundler');
  const staging = `${target}.${process.pid}.tmp`;
  rmSync(staging, {recursive: true, force: true});
  const started = Date.now();
  await bundle({entryPoint: path.join(ROOT, 'src', 'index.js'), outDir: staging, enableCaching: false});
  mkdirSync(path.dirname(target), {recursive: true});
  try {
    renameSync(staging, target);
  } catch {
    rmSync(staging, {recursive: true, force: true}); // construit en parallèle par un autre rendu : on garde le sien
  }
  console.log(`bundle construit en ${Date.now() - started} ms`);
  return target;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args) {
    console.error('usage : lody-remotion --input <scène.json> [--still <image>] --output <fichier>');
    return 2;
  }
  const props = buildProps(path.resolve(args.input));
  const {ensureBrowser, renderMedia, renderStill, selectComposition} = await import('@remotion/renderer');
  await ensureBrowser();
  const serveUrl = await ensureBundle();
  const composition = await selectComposition({serveUrl, id: COMPOSITION, inputProps: props, logLevel: 'error'});
  const output = path.resolve(args.output);
  const started = Date.now();
  if (args.still !== undefined) {
    const frame = Math.min(Number(args.still), composition.durationInFrames - 1);
    await renderStill({composition, serveUrl, inputProps: props, frame, output, imageFormat: 'png',
      overwrite: true, logLevel: 'error'});
  } else {
    let last = -10;
    await renderMedia({
      composition, serveUrl, inputProps: props, codec: 'h264', outputLocation: output, crf: 20,
      pixelFormat: 'yuv420p', imageFormat: 'jpeg', jpegQuality: 92, concurrency: 1, muted: true,
      overwrite: true, logLevel: 'error',
      onProgress: ({progress}) => {
        const pct = Math.floor(progress * 100);
        if (pct >= last + 10) {
          last = pct;
          console.log(`progression ${pct} %`);
        }
      },
    });
  }
  console.log(`scène « ${props.scene.id} » : ${composition.durationInFrames} image(s) rendue(s) en `
    + `${Date.now() - started} ms`);
  return 0;
}

main().then((code) => process.exit(code), (error) => {
  console.error(error instanceof InputError ? `entrée refusée : ${error.message}` : `échec du rendu : ${error.message}`);
  process.exit(1);
});
