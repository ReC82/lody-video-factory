// Test d'export reproductible : rend deux fois les mêmes images avec Remotion et compare les
// empreintes. Lourd (Chrome headless) : n'est lancé que si RENDER_TEST=1.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const run = process.env.RENDER_TEST === '1';

test('deux rendus des mêmes images sont identiques octet pour octet', { skip: !run && 'RENDER_TEST=1 pour lancer' }, () => {
  const dir = mkdtempSync(join(tmpdir(), 'remotion-118-'));
  try {
    const hashes = {};
    for (const pass of [1, 2]) {
      for (const frame of [45, 200, 410]) {
        const out = join(dir, `${pass}-${frame}.png`);
        execFileSync('npx', ['remotion', 'still', 'src/index.ts', 'LodyProto118', out, `--frame=${frame}`, '--log=error'], { stdio: 'inherit' });
        const h = createHash('sha256').update(readFileSync(out)).digest('hex');
        if (pass === 1) hashes[frame] = h;
        else assert.equal(h, hashes[frame], `image ${frame}`);
      }
    }
    assert.notEqual(hashes[45], hashes[200]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
