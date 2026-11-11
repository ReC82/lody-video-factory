// Lecture d'un WAV PCM 16 bits et enveloppe d'énergie par image (synchro labiale, #150).
// Aucune dépendance : le worker #120 fournit `voice.wav` (voix seule, sans musique).

/** Décode un WAV PCM 16 bits (mono ou multicanal) en échantillons mono flottants [-1, 1]. */
export function decodeWav(buffer) {
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  const tag = (offset) => String.fromCharCode(...buffer.subarray(offset, offset + 4));
  if (buffer.length < 12 || tag(0) !== 'RIFF' || tag(8) !== 'WAVE') throw new Error('WAV attendu');
  let offset = 12;
  let format = null;
  while (offset + 8 <= buffer.length) {
    const id = tag(offset);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === 'fmt ') {
      format = {
        audioFormat: view.getUint16(body, true),
        channels: view.getUint16(body + 2, true),
        sampleRate: view.getUint32(body + 4, true),
        bits: view.getUint16(body + 14, true),
      };
    } else if (id === 'data') {
      if (!format || format.audioFormat !== 1 || format.bits !== 16) throw new Error('WAV PCM 16 bits attendu');
      const end = Math.min(buffer.length, body + size);
      const frames = Math.floor((end - body) / (2 * format.channels));
      const samples = new Float32Array(frames);
      for (let i = 0; i < frames; i++) {
        let sum = 0;
        for (let c = 0; c < format.channels; c++) sum += view.getInt16(body + (i * format.channels + c) * 2, true);
        samples[i] = sum / format.channels / 32768;
      }
      return {sampleRate: format.sampleRate, samples};
    }
    offset = body + size + (size % 2);
  }
  throw new Error('WAV sans données');
}

/**
 * Enveloppe RMS par image, normalisée sur TOUT le fichier (même échelle d'une scène à l'autre), lissée
 * (attaque rapide, relâche plus lente), puis découpée sur la fenêtre [offset, offset + count[ en images.
 */
export function envelope({sampleRate, samples}, fps, offset = 0, count = null) {
  const per = sampleRate / fps;
  const total = Math.ceil(samples.length / per);
  const rms = new Float32Array(total);
  for (let frame = 0; frame < total; frame++) {
    const start = Math.floor(frame * per);
    const end = Math.min(samples.length, Math.floor((frame + 1) * per));
    let sum = 0;
    for (let i = start; i < end; i++) sum += samples[i] * samples[i];
    rms[frame] = end > start ? Math.sqrt(sum / (end - start)) : 0;
  }
  const sorted = [...rms].filter((v) => v > 0.005).sort((a, b) => a - b);
  const reference = sorted.length ? sorted[Math.floor(sorted.length * 0.95)] : 1;
  const out = [];
  let smooth = 0;
  for (let frame = 0; frame < total; frame++) {
    const value = Math.min(1, rms[frame] / (reference || 1));
    smooth = value > smooth ? value : smooth * 0.55 + value * 0.45;
    out.push(Number(smooth.toFixed(4)));
  }
  const length = count ?? Math.max(0, total - offset);
  return Array.from({length}, (_, i) => out[offset + i] ?? 0);
}
