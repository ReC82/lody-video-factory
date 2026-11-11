// Fonctions PURES du moteur de scène Lody (#150) : interpolation des images-clés, transitions, caméra et
// pose de la marionnette `lody.rig/1` à une image donnée. Aucune dépendance (ni React ni Remotion) :
// testées directement par `node --test` et importées telles quelles par la composition Remotion.
//
// Tout ce qui est lu vient du JSON déclaratif `lody.animation-scene/1` (#120) et du rig de l'asset (#136) :
// aucun code n'est généré ni évalué depuis ces données.

export const EASINGS = ['linear', 'ease_in', 'ease_out', 'ease_in_out', 'spring'];
export const RELEASE_FRAMES = 6;      // retour au repos après un geste (évite un saut de pose)
export const BLINK_EVERY = 104;       // clignement automatique (~3,5 s à 30 i/s), décalé par objet
export const POSE_KEYS = ['select', 'rotate', 'pupils', 'lift', 'squash', 'air', 'accessories'];

const clamp01 = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t);

/** Courbe d'accélération du contrat (`easing`), t ∈ [0, 1] → [0, 1] (le ressort dépasse légèrement 1). */
export function ease(name, t) {
  const x = clamp01(t);
  switch (name) {
    case 'linear':
      return x;
    case 'ease_in':
      return x * x * x;
    case 'ease_out':
      return 1 - (1 - x) ** 3;
    case 'spring':
      // Ressort amorti : dépasse la cible puis s'y pose ; vaut exactement 0 et 1 aux bornes.
      return x >= 1 ? 1 : 1 - Math.exp(-6 * x) * Math.cos(10 * x) * (1 - x);
    case 'ease_in_out':
    default:
      return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
  }
}

/**
 * Valeur d'un champ numérique d'une liste d'images-clés à l'image `frame`.
 * Avant la première clé : valeur de la première ; après la dernière : valeur de la dernière.
 */
export function sample(keyframes, frame, field, easing = 'ease_in_out', fallback = 0) {
  if (!Array.isArray(keyframes) || keyframes.length === 0) return fallback;
  if (frame <= keyframes[0].frame) return keyframes[0][field];
  const last = keyframes[keyframes.length - 1];
  if (frame >= last.frame) return last[field];
  for (let i = 0; i < keyframes.length - 1; i++) {
    const a = keyframes[i];
    const b = keyframes[i + 1];
    if (frame >= a.frame && frame < b.frame) {
      const t = ease(easing, (frame - a.frame) / (b.frame - a.frame));
      return a[field] + (b[field] - a[field]) * t;
    }
  }
  return last[field];
}

/** Fenêtre de visibilité d'un objet (`start` inclus, `end` exclu). */
export function isVisible(obj, frame, duration) {
  const start = obj.start ?? 0;
  const end = obj.end ?? duration;
  return frame >= start && frame < end;
}

/** État d'un objet à l'image `frame` : position, échelle, rotation, opacité. */
export function objectState(obj, frame) {
  const easing = obj.easing ?? 'ease_in_out';
  return {
    x: sample(obj.position, frame, 'x', easing),
    y: sample(obj.position, frame, 'y', easing),
    scale: sample(obj.scale, frame, 'value', easing, 1),
    rotation: sample(obj.rotation, frame, 'value', easing, 0),
    opacity: sample(obj.opacity, frame, 'value', easing, 1),
  };
}

/** Caméra : point visé (x, y), zoom et rotation ; par défaut le centre du canevas, zoom 1. */
export function cameraState(camera, frame, canvas) {
  const keys = camera?.keyframes;
  const easing = camera?.easing ?? 'ease_in_out';
  return {
    x: sample(keys, frame, 'x', easing, canvas.width / 2),
    y: sample(keys, frame, 'y', easing, canvas.height / 2),
    zoom: sample(keys, frame, 'zoom', easing, 1),
    rotation: sample(keys, frame, 'rotation', easing, 0),
  };
}

/** Effet des transitions d'entrée/sortie à l'image `frame` : {opacity, dx, dy, scale}. */
export function transitionState(render, frame, duration, canvas) {
  const out = {opacity: 1, dx: 0, dy: 0, scale: 1};
  const apply = (transition, progress) => {
    // progress : 0 = scène invisible / hors champ, 1 = scène pleinement présente.
    const p = ease('ease_in_out', progress);
    switch (transition.type) {
      case 'fade':
        out.opacity *= p;
        break;
      case 'slide_left':
        out.dx += (1 - p) * canvas.width;
        break;
      case 'slide_right':
        out.dx -= (1 - p) * canvas.width;
        break;
      case 'slide_up':
        out.dy += (1 - p) * canvas.height;
        break;
      case 'zoom':
        out.scale *= 0.85 + 0.15 * p;
        out.opacity *= p;
        break;
      default:
        break;
    }
  };
  const tin = render.transition_in;
  if (tin && tin.type !== 'cut' && frame < tin.frames) apply(tin, frame / tin.frames);
  const tout = render.transition_out;
  if (tout && tout.type !== 'cut' && frame >= duration - tout.frames) {
    apply(tout, (duration - 1 - frame) / tout.frames);
  }
  return out;
}

// -- marionnette lody.rig/1 ---------------------------------------------------------------------------------

/** Fusion « maintien » (même règle que scripts/animation/rig_pose.py) : une clé absente garde sa valeur. */
export function mergePoses(...poses) {
  const out = {};
  for (const pose of poses) {
    for (const [key, value] of Object.entries(pose ?? {})) {
      if (!POSE_KEYS.includes(key)) throw new Error(`clé de pose inconnue : ${key}`);
      if (value && typeof value === 'object' && !Array.isArray(value)) out[key] = {...(out[key] ?? {}), ...value};
      else out[key] = Array.isArray(value) ? value.map((v) => (typeof v === 'object' ? {...v} : v)) : value;
    }
  }
  return out;
}

/** Interpolation numérique de deux poses complètes ; variantes et accessoires changent à mi-parcours. */
export function lerpPose(a, b, t) {
  const num = (x, y) => (x ?? 0) + ((y ?? 0) - (x ?? 0)) * t;
  const rotate = {};
  for (const part of new Set([...Object.keys(a.rotate ?? {}), ...Object.keys(b.rotate ?? {})])) {
    rotate[part] = num(a.rotate?.[part], b.rotate?.[part]);
  }
  const pa = a.pupils ?? [0, 0];
  const pb = b.pupils ?? [0, 0];
  const discrete = t < 0.5 ? a : b;
  return {
    select: {...(discrete.select ?? {})},
    rotate,
    pupils: [num(pa[0], pb[0]), num(pa[1], pb[1])],
    lift: num(a.lift, b.lift),
    squash: num(a.squash, b.squash),
    air: num(a.air, b.air),
    accessories: [...(discrete.accessories ?? [])],
  };
}

/** Dernier changement d'expression à l'image `frame` (ou null). */
export function expressionAt(obj, frame) {
  let name = null;
  for (const event of obj.expressions ?? []) if (event.frame <= frame) name = event.name;
  return name;
}

/** Pose d'un geste à `local` images de son début : interpolation entre ses poses-clés cumulées. */
export function gesturePose(rig, gesture, base, local) {
  const spec = rig.gestures?.[gesture];
  if (!spec) return base;
  const cumulative = [];
  let acc = base;
  for (const key of spec.keys) {
    acc = mergePoses(acc, key.pose);
    cumulative.push({frame: key.frame, pose: acc});
  }
  if (local <= cumulative[0].frame) return cumulative[0].pose;
  for (let i = 0; i < cumulative.length - 1; i++) {
    const a = cumulative[i];
    const b = cumulative[i + 1];
    if (local >= a.frame && local < b.frame) {
      return lerpPose(a.pose, b.pose, ease('ease_in_out', (local - a.frame) / (b.frame - a.frame)));
    }
  }
  return cumulative[cumulative.length - 1].pose;
}

/**
 * Temps local d'un geste : joué à sa vitesse naturelle (`frames` du rig) ; compressé si la fenêtre du
 * storyboard est plus courte. Au-delà de sa durée, la dernière pose est tenue jusqu'à la fin de la fenêtre.
 */
export function gestureLocal(spec, event, frame) {
  const window = event.end - event.start;
  const speed = window < spec.frames ? spec.frames / window : 1;
  return Math.min(spec.frames, (frame - event.start) * speed);
}

// Lettres → visèmes du rig Voltix (visèmes français de #136). Les digrammes sont lus avant les lettres seules.
const DIGRAMS = [['ou', 'ou'], ['ch', 'ou'], ['au', 'o'], ['eau', 'o'], ['on', 'o'], ['an', 'o'], ['en', 'o'],
  ['eu', 'e'], ['ai', 'e'], ['ei', 'e'], ['oi', 'a'], ['ph', 'fv']];
const LETTERS = {
  a: 'a', à: 'a', â: 'a', e: 'e', é: 'e', è: 'e', ê: 'e', ë: 'e', i: 'i', î: 'i', ï: 'i', y: 'i', o: 'o', ô: 'o',
  u: 'ou', ù: 'ou', û: 'ou', m: 'mbp', b: 'mbp', p: 'mbp', f: 'fv', v: 'fv', j: 'ou', w: 'ou',
  s: 'i', z: 'i', t: 'i', d: 'i', n: 'i', l: 'i', k: 'i', g: 'i', r: 'i', c: 'i', q: 'i', x: 'i', ç: 'i',
};

/** Suite de visèmes d'un texte (ponctuation et espaces ignorés). */
export function visemesOf(text) {
  const lower = String(text).toLowerCase();
  const out = [];
  let i = 0;
  while (i < lower.length) {
    const digram = DIGRAMS.find(([key]) => lower.startsWith(key, i));
    if (digram) {
      if (digram[1] !== out[out.length - 1]) out.push(digram[1]);
      i += digram[0].length;
      continue;
    }
    const v = LETTERS[lower[i]];
    if (v && v !== out[out.length - 1]) out.push(v);
    i += 1;
  }
  return out.length ? out : ['a'];
}

export const SPEAKING_THRESHOLD = 0.12;

/**
 * Bouche à l'image `frame` : null si la marionnette ne parle pas (la bouche de l'expression reste),
 * sinon un visème du rig. Pendant un repère de parole, l'enveloppe audio (0-1) décide si la bouche est
 * ouverte ; le visème suit les lettres du repère, réparties sur sa durée. Sans audio, les lettres seules.
 */
export function mouthAt(cues, envelope, frame) {
  const cue = (cues ?? []).find((c) => frame >= c.start && frame < c.end);
  if (!cue) return null;
  if (envelope && envelope.length) {
    const level = envelope[Math.min(frame, envelope.length - 1)] ?? 0;
    if (level < SPEAKING_THRESHOLD) return 'mbp';
  }
  const visemes = visemesOf(cue.text);
  const position = (frame - cue.start) / Math.max(1, cue.end - cue.start);
  const index = Math.min(visemes.length - 1, Math.floor(position * visemes.length));
  return visemes[index];
}

/** Clignement automatique : paupières de l'image `frame` (null hors clignement). */
export function autoBlink(frame, seed = 0) {
  const phase = (frame + seed * 37) % BLINK_EVERY;
  if (phase === 1 || phase === 4) return 'mi-clos';
  if (phase === 2 || phase === 3) return 'fermes';
  return null;
}

/**
 * Pose complète d'un objet marionnette à l'image `frame` :
 * repos → expression courante → geste actif (ou retour au repos) → respiration → clignement → bouche.
 */
export function puppetPose(rig, obj, frame, {cues = null, envelope = null, seed = 0} = {}) {
  const expression = expressionAt(obj, frame);
  let base = mergePoses(rig.rest, expression ? rig.expressions?.[expression] : {});
  let pose = base;
  let lidsDriven = false;
  for (const event of obj.gestures ?? []) {
    const spec = rig.gestures?.[event.name];
    if (!spec) continue;
    if (frame >= event.start && frame < event.end) {
      pose = gesturePose(rig, event.name, base, gestureLocal(spec, event, frame));
      lidsDriven = event.name === 'cligner';
      break;
    }
    if (frame >= event.end && frame < event.end + RELEASE_FRAMES) {
      const last = gesturePose(rig, event.name, base, spec.frames);
      pose = lerpPose(last, mergePoses(base, {accessories: []}),
        ease('ease_in_out', (frame - event.end + 1) / RELEASE_FRAMES));
      break;
    }
  }
  pose = lerpPose(pose, pose, 0); // forme complète (tous les champs)
  // Respiration : écrasement très léger, jamais pendant un saut.
  if (!pose.air) pose.squash += 0.012 * Math.sin((frame + seed * 11) / 14);
  if (!lidsDriven) {
    const lids = autoBlink(frame, seed);
    if (lids) pose.select.lids = lids;
  }
  if (obj.lipsync) {
    const mouth = mouthAt(cues, envelope, frame);
    if (mouth) pose.select['mouth-visemes'] = mouth;
  }
  return pose;
}
