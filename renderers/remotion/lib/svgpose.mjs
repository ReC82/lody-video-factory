// Application d'une pose `lody.rig/1` à un SVG de marionnette, dans le navigateur de rendu (DOM standard).
// Portage fidèle de `apply_pose` (scripts/animation/rig_pose.py, #136) : rotations autour des `data-pivot`,
// variantes `data-variants`/`data-variant`, regard, ressorts (`lift`), écrasement (`squash`), saut (`air`)
// et accessoires accrochés aux `data-slot`. Seuls des attributs `transform`/`display` sont modifiés :
// aucun script, aucun contenu distant n'est introduit.

const SVG_NS = 'http://www.w3.org/2000/svg';

function index(root) {
  const ids = new Map();
  for (const el of root.querySelectorAll('[id]')) ids.set(el.getAttribute('id'), el);
  return ids;
}

function pivot(el) {
  const raw = el.getAttribute('data-pivot');
  if (!raw) throw new Error(`« ${el.getAttribute('id')} » n'a pas de data-pivot`);
  const [x, y] = raw.trim().split(/\s+/).map(Number);
  return [x, y];
}

function prepend(el, transform) {
  const current = el.getAttribute('transform');
  el.setAttribute('transform', current ? `${transform} ${current}` : transform);
}

const f = (v) => Number(v.toFixed(3));

/** Supprime ce qui n'a rien à faire dans un rendu (scripts, liens externes, gestionnaires d'événements). */
export function sanitize(root) {
  for (const el of [...root.querySelectorAll('script, foreignObject')]) el.remove();
  for (const el of [root, ...root.querySelectorAll('*')]) {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim().toLowerCase();
      if (name.startsWith('on')) el.removeAttribute(attr.name);
      else if ((name === 'href' || name === 'xlink:href') && !value.startsWith('#') && !value.startsWith('data:image/')) {
        el.removeAttribute(attr.name);
      }
    }
  }
  return root;
}

/** Préfixe identifiants et références `url(#…)`/`href="#…"` : plusieurs copies cohabitent sans collision. */
export function prefixIds(root, prefix) {
  for (const el of [root, ...root.querySelectorAll('*')]) {
    if (el.hasAttribute('id')) el.setAttribute('id', prefix + el.getAttribute('id'));
    for (const attr of [...el.attributes]) {
      if (attr.value.includes('url(#')) {
        el.setAttribute(attr.name, attr.value.replace(/url\(#([^)]+)\)/g, (_, id) => `url(#${prefix}${id})`));
      } else if ((attr.name === 'href' || attr.name === 'xlink:href') && attr.value.startsWith('#')) {
        el.setAttribute(attr.name, `#${prefix}${attr.value.slice(1)}`);
      }
    }
  }
  return root;
}

export function parseSvg(text) {
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
  const root = doc.documentElement;
  if (!root || root.nodeName !== 'svg') throw new Error('SVG illisible');
  return sanitize(root);
}

function propGroup(document, propText, anchor, at, scale, rotate) {
  const prop = parseSvg(propText);
  const [, , width, height] = prop.getAttribute('viewBox').split(/\s+/).map(Number);
  const group = document.createElementNS(SVG_NS, 'g');
  group.setAttribute('transform', `translate(${at[0]} ${at[1]}) rotate(${rotate}) scale(${scale}) `
    + `translate(${-anchor[0] * width} ${-anchor[1] * height})`);
  for (const child of [...prop.childNodes]) {
    if (child.nodeName !== 'title') group.appendChild(document.importNode(child, true));
  }
  return group;
}

/**
 * Applique `pose` à une COPIE de `template` (racine SVG déjà parsée) et la renvoie.
 * `props` : nom d'accessoire → {svg, anchor: [x, y]} pour les accessoires de la pose.
 */
export function applyPose(template, pose, props = {}) {
  const root = template.cloneNode(true);
  const ids = index(root);
  const need = (name) => {
    const el = ids.get(name);
    if (!el) throw new Error(`groupe « ${name} » absent du SVG`);
    return el;
  };

  for (const [group, variant] of Object.entries(pose.select ?? {})) {
    const parent = need(group);
    for (const child of parent.children) {
      const name = child.getAttribute('data-variant');
      if (name === null) continue;
      if (name === variant) child.removeAttribute('display');
      else child.setAttribute('display', 'none');
    }
  }

  for (const [group, angle] of Object.entries(pose.rotate ?? {})) {
    if (!angle) continue;
    const el = need(group);
    const [x, y] = pivot(el);
    prepend(el, `rotate(${f(angle)} ${x} ${y})`);
  }

  const [dx, dy] = pose.pupils ?? [0, 0];
  if (dx || dy) for (const side of ['left', 'right']) prepend(need(`pupil-${side}`), `translate(${f(dx)} ${f(dy)})`);

  const lift = pose.lift ?? 0;
  const squash = pose.squash ?? 0;
  const air = pose.air ?? 0;
  if (lift || squash) {
    const upper = need('upper');
    const [px, py] = pivot(upper);
    prepend(upper, `translate(0 ${f(-lift)}) translate(${px} ${py}) scale(${f(1 + squash)} ${f(1 - squash)}) `
      + `translate(${-px} ${-py})`);
    for (const side of ['left', 'right']) {
      const spring = ids.get(`springs-${side}`);
      if (!spring) continue; // marionnette sans ressorts (personnage humain, #173) : jambes inchangées
      const [sx, sy] = pivot(spring);
      const length = Number(spring.getAttribute('data-length'));
      const ratio = Math.max(0.2, (length + lift) / length);
      prepend(spring, `translate(${sx} ${sy}) scale(1 ${f(ratio)}) translate(${-sx} ${-sy})`);
    }
  }
  if (air) {
    prepend(need('puppet'), `translate(0 ${f(-air)})`);
    const shadow = ids.get('shadow');
    if (shadow) {
      const k = Math.max(0.4, 1 - air / 250);
      prepend(shadow, `translate(300 884) scale(${f(k)} ${f(k)}) translate(-300 -884)`);
    }
  }

  for (const item of pose.accessories ?? []) {
    const slot = need(item.slot);
    const prop = props[item.asset];
    if (!prop) continue; // accessoire non fourni à cette scène : la pose reste valable sans lui
    slot.appendChild(propGroup(root.ownerDocument, prop.svg, prop.anchor, pivot(slot), item.scale ?? 1,
      item.rotate ?? 0));
  }
  return root;
}
