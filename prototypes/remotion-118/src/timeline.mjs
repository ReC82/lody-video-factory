// Composition temporelle du prototype #118 — fonctions PURES (aucune dépendance React/Remotion),
// partagées par la scène (src/Scene.tsx), le générateur d'audio factice (scripts/make-audio.mjs)
// et les tests (`node --test test/`). Toute grandeur animée est une fonction du numéro d'image :
// c'est ce qui rend le rendu déterministe et reproductible image par image.

export const FPS = 30;
export const DURATION_IN_FRAMES = 450; // 15 s
export const WIDTH = 1080;
export const HEIGHT = 1920;
export const SAMPLE_RATE = 48000;

export const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const easeInOut = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

/**
 * Interpolation par images-clés : `keys` = [[image, valeur], …] triées par image.
 * Avant la première / après la dernière clé, la valeur est maintenue. Lissage ease-in-out.
 */
export function keyframes(frame, keys, ease = easeInOut) {
  if (keys.length === 0) throw new Error('keyframes: liste vide');
  if (frame <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [f1, v1] = keys[i];
    if (frame <= f1) {
      const [f0, v0] = keys[i - 1];
      const t = f1 === f0 ? 1 : (frame - f0) / (f1 - f0);
      return lerp(v0, v1, ease(clamp(t)));
    }
  }
  return keys[keys.length - 1][1];
}

// ---------------------------------------------------------------- Texte prononcé (audio factice)
// Chaque mot porte son nombre de syllabes ; une syllabe dure SYLLABLE images, dont VOICED voisées.
export const SYLLABLE = 7;
export const VOICED = 5;
export const WORD_GAP = 3;
export const PHRASES = [
  { start: 30, words: [['Bonjour', 2], ['!', 0], ['Je', 1], ['suis', 1], ['Mimo', 2]] },
  { start: 150, words: [['Je', 1], ['bouge', 1], ['vraiment,', 2], ['image', 2], ['par', 1], ['image', 2]] },
  { start: 300, words: [['Rendu', 2], ['en', 1], ['2D', 2], ['avec', 2], ['Remotion', 3]] },
];

/** Mots avec images de début/fin et syllabes [début, fin) voisées. */
export function buildWords() {
  const words = [];
  for (const [p, phrase] of PHRASES.entries()) {
    let f = phrase.start;
    for (const [text, syl] of phrase.words) {
      const syllables = [];
      for (let s = 0; s < syl; s++) {
        syllables.push([f + s * SYLLABLE, f + s * SYLLABLE + VOICED]);
      }
      const end = f + syl * SYLLABLE;
      words.push({ phrase: p, text, start: f, end, syllables });
      if (syl > 0) f = end + WORD_GAP;
    }
  }
  return words;
}

export const WORDS = buildWords();
export const SYLLABLES = WORDS.flatMap((w) => w.syllables);

export function phraseBounds(p) {
  const ws = WORDS.filter((w) => w.phrase === p);
  return [ws[0].start, ws[ws.length - 1].end];
}

/** Hauteur (Hz) de la syllabe n : mélodie simple et déterministe. */
export const syllablePitch = (n) => 170 + 40 * Math.sin(n * 1.7) + (n % 3) * 12;

// ---------------------------------------------------------------- Bouche (synchro audio)
/**
 * Ouverture de la bouche 0..1 à partir de l'enveloppe RMS par image dérivée du WAV
 * (src/envelope.json). On lisse avec l'image précédente pour éviter les sauts, et on applique
 * un seuil pour que la bouche se ferme vraiment dans les silences.
 */
export function mouthOpen(frame, envelope) {
  const at = (f) => (f >= 0 && f < envelope.length ? envelope[f] : 0);
  const v = 0.7 * at(frame) + 0.3 * at(frame - 1);
  return clamp((v - 0.04) / 0.8);
}

// ---------------------------------------------------------------- Yeux
export const BLINKS = [12, 96, 140, 212, 226, 296, 372, 432]; // 212/226 : double clignement
/** Fermeture de la paupière 0 (ouverte) .. 1 (fermée) : 2 images pour fermer, 1 tenue, 3 pour rouvrir. */
export function blink(frame) {
  let closed = 0;
  for (const b of BLINKS) {
    const d = frame - b;
    let c = 0;
    if (d >= 0 && d < 2) c = (d + 1) / 2;
    else if (d >= 2 && d < 3) c = 1;
    else if (d >= 3 && d < 6) c = 1 - (d - 2) / 4;
    closed = Math.max(closed, c);
  }
  return closed;
}

/** Regard (décalage de la pupille, -1..1) : vers le spectateur, le graphique, l'oiseau… */
export function gaze(frame) {
  const x = keyframes(frame, [[0, 0], [88, 0], [96, -0.3], [118, -0.3], [126, 0], [148, 0], [160, 0.85], [236, 0.85], [246, 0], [256, 0], [266, -0.8], [292, -0.8], [302, 0], [450, 0]]);
  const y = keyframes(frame, [[0, 0], [88, 0], [96, 0.4], [118, 0.4], [126, 0], [148, 0], [160, -0.55], [236, -0.55], [246, 0], [256, 0], [266, -0.7], [292, -0.7], [302, 0], [450, 0]]);
  // micro-saccades pour éviter un regard figé
  const jitter = 0.06 * Math.sin(frame * 0.9) * Math.sin(frame * 0.23);
  return { x: clamp(x + jitter, -1, 1), y: clamp(y, -1, 1) };
}

// ---------------------------------------------------------------- Bras
/** Bras droit (côté graphique) : salut au début, désigne le graphique, salut d'au revoir. Degrés. */
export function armRight(frame) {
  const raise = keyframes(frame, [[0, -18], [24, -18], [36, -120], [96, -120], [110, -18], [150, -18], [162, -75], [246, -75], [260, -18], [390, -18], [402, -125], [450, -125]]);
  const waving = (frame >= 36 && frame < 96) || frame >= 402;
  const wave = waving ? 22 * Math.sin((frame - 36) * 0.55) : 0;
  return { shoulder: raise, elbow: waving ? -25 + wave : raise < -30 ? -10 : 12 + 4 * Math.sin(frame * 0.12) };
}
/** Bras gauche : léger balancement de repos (respiration). */
export const armLeft = (frame) => ({ shoulder: -20 + 6 * Math.sin(frame * 0.12), elbow: 12 + 5 * Math.sin(frame * 0.12 + 1) });

/** Respiration et hochement de tête (parole). */
export const breathe = (frame) => 1 + 0.018 * Math.sin(frame * 0.14);
export const headBob = (frame, open) => 4 * Math.sin(frame * 0.5) * open + 2 * Math.sin(frame * 0.07);

// ---------------------------------------------------------------- Décor
/** Position horizontale d'un nuage qui traverse l'écran en boucle (px). */
export const cloudX = (frame, speed, offset, width = WIDTH) => {
  const span = width + 600;
  return (((offset + frame * speed) % span) + span) % span - 300;
};
/** Inclinaison d'un brin d'herbe (degrés) : vent + rafale. */
export const grassSway = (frame, i) => 6 * Math.sin(frame * 0.11 + i * 0.7) + 4 * Math.sin(frame * 0.031 + i * 0.13);
/** Battement d'ailes d'oiseau -1..1. */
export const wingFlap = (frame, phase = 0) => Math.sin(frame * 0.6 + phase);
/** Soleil : montée lente et rotation des rayons. */
export const sun = (frame) => ({ y: keyframes(frame, [[0, 640], [450, 480]], (t) => t), rays: frame * 0.8 });

// ---------------------------------------------------------------- Graphique et texte
export const BAR_TARGETS = [0.45, 0.8, 0.62, 0.95];
/** Hauteur relative d'une barre : chaque barre pousse à son tour à partir de l'image 160. */
export const barHeight = (frame, i) => BAR_TARGETS[i] * keyframes(frame, [[160 + i * 12, 0], [190 + i * 12, 1]]);
/** Anneau de progression 0..1 sur toute la vidéo. */
export const progress = (frame) => clamp(frame / (DURATION_IN_FRAMES - 1));
/** Nombre de lettres visibles du titre (apparition lettre par lettre). */
export const titleLetters = (frame, text, start = 6, perLetter = 2) => clamp(Math.floor((frame - start) / perLetter) + 1, 0, text.length);
/** Mot en cours (karaoké) ou null. */
export const currentWordIndex = (frame) => WORDS.findIndex((w) => frame >= w.start && frame < w.end + WORD_GAP);
