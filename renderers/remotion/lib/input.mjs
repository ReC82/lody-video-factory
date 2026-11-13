// Entrée de scène `lody.animation-scene/1` (écrite par le worker #120) → props de la composition Remotion.
// Les fichiers sont lus ICI, côté Node, aux chemins fournis par le worker (copies vérifiées du dossier de la
// tâche) ; le navigateur de rendu ne reçoit que leur contenu : il n'accède jamais au disque.

import {readFileSync} from 'node:fs';
import path from 'node:path';
import {decodeWav, envelope} from './wav.mjs';

export const SCENE_SCHEMA = 'lody.animation-scene/1';
const RASTER = {'.png': 'image/png', '.webp': 'image/webp'};
const MAX_FILE = 8 * 1024 * 1024;

export class InputError extends Error {}

function readLimited(file) {
  const data = readFileSync(file);
  if (data.length > MAX_FILE) throw new InputError(`fichier d'asset trop volumineux : ${path.basename(file)}`);
  return data;
}

function viewBoxOf(svg) {
  const match = /viewBox\s*=\s*["']([^"']+)["']/.exec(svg);
  if (!match) return null;
  const values = match[1].trim().split(/[\s,]+/).map(Number);
  return values.length === 4 && values.every(Number.isFinite) ? values : null;
}

/** Contenu des fichiers d'un asset : SVG (texte), rig `lody.rig/1` (JSON), image matricielle (data URI). */
export function loadAsset(files, info = {}) {
  const out = {
    kind: info.kind ?? null, slug: info.slug ?? null, width: info.width ?? null, height: info.height ?? null,
    anchor: info.anchor ? [info.anchor.x, info.anchor.y] : null, svg: {}, rig: null, image: null, viewBox: null,
  };
  for (const [role, file] of Object.entries(files)) {
    const ext = path.extname(file).toLowerCase();
    if (ext === '.svg') {
      out.svg[role] = readLimited(file).toString('utf8');
      if (role === 'base') out.viewBox = viewBoxOf(out.svg[role]);
    } else if (ext === '.json') {
      const data = JSON.parse(readLimited(file).toString('utf8'));
      if (data && data.schema === 'lody.rig/1') out.rig = data;
    } else if (RASTER[ext] && (role === 'base' || !out.image)) {
      out.image = `data:${RASTER[ext]};base64,${readLimited(file).toString('base64')}`;
    }
  }
  if (out.rig && !out.anchor) out.anchor = [0.5, 0.98]; // marionnette : pieds au sol par défaut
  return out;
}

/** Lit et vérifie l'entrée de scène, charge ses assets et l'enveloppe de la voix ; renvoie les props. */
export function buildProps(inputPath, {captions = true} = {}) {
  let data;
  try {
    data = JSON.parse(readFileSync(inputPath, 'utf8'));
  } catch {
    throw new InputError('entrée de scène illisible');
  }
  if (!data || data.schema !== SCENE_SCHEMA) throw new InputError(`schéma ${SCENE_SCHEMA} attendu`);
  const {canvas, scene} = data;
  if (!canvas || !scene || !Number.isInteger(scene.duration_frames)) throw new InputError('scène incomplète');
  const assets = {};
  for (const [key, files] of Object.entries(data.assets ?? {})) {
    assets[key] = loadAsset(files, (data.asset_info ?? {})[key] ?? {});
  }
  let env = null;
  if (data.voice?.path) {
    try {
      env = envelope(decodeWav(readFileSync(data.voice.path)), canvas.fps, data.voice.offset_frames ?? 0,
        scene.duration_frames);
    } catch (error) {
      throw new InputError(`voix illisible pour la synchro labiale (${error.message})`);
    }
  }
  return {canvas, scene, assets, envelope: env, captions, scale: renderScale(data)};
}

/** Échelle de rendu (#121) : `render.scale` dans [0.25, 1[ = prévisualisation basse résolution ; 1 sinon. */
export function renderScale(data) {
  const value = Number(data?.render?.scale);
  return Number.isFinite(value) && value >= 0.25 && value < 1 ? value : 1;
}
