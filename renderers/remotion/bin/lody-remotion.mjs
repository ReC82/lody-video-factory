#!/usr/bin/env node
// Moteur de scène 2D de Lody (#150), appelé par le worker #120 via LODY_ANIMATION_RENDERER :
//
//   lody-remotion --input scenes/NN-input.json --output scenes/NN.render.mp4
//   lody-remotion --input scenes/NN-input.json --still <image> --output scenes/NN-still.png
//   lody-remotion --version      → « lody-remotion <empreinte des sources> » (identité pour le cache de scène #121)
//   lody-remotion --prepare      (image Docker #154 : télécharge Chrome headless et préconstruit le bundle)
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
  if (argv.length === 1 && argv[0] === '--prepare') return {prepare: true};
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
  if (process.argv.length === 3 && process.argv[2] === '--version') {
    console.log(`lody-remotion ${sourceDigest()}`); // le worker #121 invalide son cache quand les sources changent
    return 0;
  }
  const args = parseArgs(process.argv.slice(2));
  if (!args) {
    console.error('usage : lody-remotion --input <scène.json> [--still <image>] --output <fichier> | --prepare | --version');
    return 2;
  }
  // Chemins résolus AVANT de changer de dossier : le worker lance le moteur depuis le dossier de la tâche.
  const input = args.prepare ? null : path.resolve(args.input);
  const output = args.prepare ? null : path.resolve(args.output);
  // Remotion range Chrome headless dans `node_modules/.remotion` du package.json le plus proche du dossier
  // COURANT : depuis le dossier d'une tâche, il le retéléchargerait (~100 Mo) à chaque rendu (#154).
  process.chdir(ROOT);
  const {ensureBrowser, renderMedia, renderStill, selectComposition} = await import('@remotion/renderer');
  if (args.prepare) {
    await ensureBrowser();
    console.log(`moteur prêt : bundle ${path.relative(ROOT, await ensureBundle())}`);
    return 0;
  }
  const props = buildProps(input);
  await ensureBrowser();
  const serveUrl = await ensureBundle();
  const composition = await selectComposition({serveUrl, id: COMPOSITION, inputProps: props, logLevel: 'error'});
  const started = Date.now();
  if (args.still !== undefined) {
    const frame = Math.min(Number(args.still), composition.durationInFrames - 1);
    await renderStill({composition, serveUrl, inputProps: props, frame, output, imageFormat: 'png',
      scale: props.scale, overwrite: true, logLevel: 'error'});
  } else {
    let last = -10;
    await renderMedia({
      composition, serveUrl, inputProps: props, codec: 'h264', outputLocation: output, crf: 20,
      pixelFormat: 'yuv420p', imageFormat: 'jpeg', jpegQuality: 92, concurrency: 1, muted: true,
      scale: props.scale, // #121 : prévisualisation basse résolution (1 = rendu final)
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
