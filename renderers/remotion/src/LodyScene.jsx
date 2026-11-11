// Composition Remotion qui INTERPRÈTE une scène `lody.animation-scene/1` (#120) — aucun code n'est généré
// depuis le storyboard : ce composant, écrit et relu à l'avance, lit uniquement des données déclaratives.
//
// Ordre de dessin : décor par défaut → calques du storyboard (premier = fond) sous la caméra → sous-titres
// de la parole (hors caméra) ; transitions d'entrée/sortie sur l'ensemble.

import React, {useMemo} from 'react';
import {AbsoluteFill, Img, useCurrentFrame} from 'remotion';
import {
  cameraState, isVisible, objectState, puppetPose, transitionState,
} from '../lib/anim.mjs';
import {applyPose, parseSvg, prefixIds} from '../lib/svgpose.mjs';

const FONT = '"DejaVu Sans", "Liberation Sans", Arial, sans-serif';

const TEXT_STYLES = {
  title: {
    fontSize: 88, fontWeight: 800, color: '#FFFFFF', WebkitTextStroke: '10px #1E2A44', paintOrder: 'stroke fill',
    letterSpacing: 1, textAlign: 'center', whiteSpace: 'nowrap',
  },
  subtitle: {
    fontSize: 54, fontWeight: 700, color: '#1E2A44', background: 'rgba(255,255,255,0.92)', padding: '14px 30px',
    borderRadius: 28, textAlign: 'center', whiteSpace: 'nowrap',
  },
  caption: {
    fontSize: 50, fontWeight: 600, color: '#FFFFFF', background: 'rgba(20,24,36,0.82)', padding: '12px 26px',
    borderRadius: 22, textAlign: 'center', whiteSpace: 'nowrap',
  },
  label: {
    fontSize: 40, fontWeight: 700, color: '#1E2A44', background: '#FFFFFF', border: '5px solid #1E2A44',
    padding: '8px 20px', borderRadius: 18, textAlign: 'center', whiteSpace: 'nowrap',
  },
};

const serializer = () => new XMLSerializer();

function sizeOf(info) {
  if (info.width && info.height) return [info.width, info.height];
  const box = info.viewBox ?? [0, 0, 400, 400];
  return [box[2], box[3]];
}

/** Boîte positionnée d'un asset : (x, y) du storyboard = point d'ancrage de l'asset. */
function Placed({info, state, children}) {
  const [w, h] = sizeOf(info);
  const [ax, ay] = info.anchor ?? [0.5, 0.5];
  return (
    <div style={{
      position: 'absolute', left: state.x - ax * w, top: state.y - ay * h, width: w, height: h,
      transformOrigin: `${ax * 100}% ${ay * 100}%`,
      transform: `rotate(${state.rotation}deg) scale(${state.scale})`, opacity: state.opacity,
    }}>
      {children}
    </div>
  );
}

function SvgMarkup({markup}) {
  return <div style={{width: '100%', height: '100%'}} dangerouslySetInnerHTML={{__html: markup}} />;
}

function fillBox(root) {
  root.setAttribute('width', '100%');
  root.setAttribute('height', '100%');
  root.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  root.setAttribute('style', 'overflow: visible');
  return root;
}

function Puppet({obj, info, frame, props: accessories, cues, envelope, seed}) {
  const template = useMemo(() => parseSvg(info.svg.base), [info.svg.base]);
  const pose = puppetPose(info.rig, obj, frame, {cues, envelope, seed});
  // Pose sur les identifiants du rig, PUIS préfixe (plusieurs marionnettes cohabitent sans collision).
  const root = prefixIds(applyPose(template, pose, accessories), `${obj.id}-`);
  return <SvgMarkup markup={serializer().serializeToString(fillBox(root))} />;
}

function StaticSvg({obj, info}) {
  const markup = useMemo(
    () => serializer().serializeToString(fillBox(prefixIds(parseSvg(info.svg.base), `${obj.id}-`))),
    [info.svg.base, obj.id],
  );
  return <SvgMarkup markup={markup} />;
}

function AssetObject({obj, info, frame, accessories, speech, envelope, seed}) {
  const state = objectState(obj, frame);
  let body;
  if (info.rig && info.svg?.base) {
    const cues = speech && speech.speaker === obj.id ? speech.cues : null;
    body = <Puppet obj={obj} info={info} frame={frame} props={accessories} cues={cues} envelope={envelope}
      seed={seed} />;
  } else if (info.svg?.base) {
    body = <StaticSvg obj={obj} info={info} />;
  } else if (info.image) {
    body = <Img src={info.image} style={{width: '100%', height: '100%', objectFit: 'contain'}} />;
  } else {
    return null;
  }
  return <Placed info={info} state={state}>{body}</Placed>;
}

function TextObject({obj, frame}) {
  const state = objectState(obj, frame);
  return (
    <div style={{
      position: 'absolute', left: state.x, top: state.y, fontFamily: FONT, opacity: state.opacity,
      transform: `translate(-50%, -50%) rotate(${state.rotation}deg) scale(${state.scale})`,
      ...TEXT_STYLES[obj.text.style],
    }}>
      {obj.text.content}
    </div>
  );
}

function Backdrop({canvas}) {
  // Décor neutre par défaut (atelier clair) : un calque « asset » de décor du storyboard le recouvre.
  return (
    <AbsoluteFill style={{background: 'linear-gradient(180deg, #DCEBFA 0%, #EEF5FC 58%, #D9DEE6 58%, #C9D0DA 100%)'}}>
      <div style={{position: 'absolute', left: 0, right: 0, top: canvas.height * 0.58 - 4, height: 8,
        background: '#B4BCC8'}} />
    </AbsoluteFill>
  );
}

function Captions({speech, frame, canvas}) {
  const cue = (speech?.cues ?? []).find((c) => frame >= c.start && frame < c.end);
  if (!cue) return null;
  return (
    <div style={{
      position: 'absolute', left: canvas.width * 0.06, right: canvas.width * 0.06, top: canvas.height * 0.80,
      display: 'flex', justifyContent: 'center',
    }}>
      <div style={{
        fontFamily: FONT, fontSize: Math.round(canvas.width * 0.056), fontWeight: 700, lineHeight: 1.25,
        color: '#FFFFFF', WebkitTextStroke: '8px #10131C', paintOrder: 'stroke fill', textAlign: 'center',
        background: 'rgba(16,19,28,0.35)', borderRadius: 24, padding: '12px 28px',
      }}>
        {cue.text}
      </div>
    </div>
  );
}

export const LodyScene = ({canvas, scene, assets, envelope, captions = true}) => {
  const frame = useCurrentFrame();
  const render = scene.render;
  const duration = scene.duration_frames;
  const cam = cameraState(render.camera, frame, canvas);
  const tr = transitionState(render, frame, duration, canvas);

  // Accessoires disponibles pour les gestes (ex. « tenir-cable ») : props de la scène, par nom court.
  const accessories = {};
  for (const info of Object.values(assets)) {
    if (info.kind === 'prop' && info.slug && info.svg?.base) {
      accessories[info.slug] = {svg: info.svg.base, anchor: info.anchor ?? [0.5, 0.5]};
    }
  }

  let seed = 0;
  const world = render.layers.map((layer) => (
    <AbsoluteFill key={layer.id}>
      {layer.objects.map((obj) => {
        seed += 1;
        if (!isVisible(obj, frame, duration)) return null;
        if (obj.type === 'text') return <TextObject key={obj.id} obj={obj} frame={frame} />;
        const key = `${obj.asset.id}@${obj.asset.version}`;
        const info = assets[key];
        if (!info) throw new Error(`asset ${key} absent de l'entrée de scène`);
        return <AssetObject key={obj.id} obj={obj} info={info} frame={frame} accessories={accessories}
          speech={render.speech} envelope={envelope} seed={seed} />;
      })}
    </AbsoluteFill>
  ));

  return (
    <AbsoluteFill style={{background: '#10131C', overflow: 'hidden'}}>
      <AbsoluteFill style={{
        opacity: tr.opacity, transformOrigin: '50% 50%',
        transform: `translate(${tr.dx}px, ${tr.dy}px) scale(${tr.scale})`,
      }}>
        <Backdrop canvas={canvas} />
        <AbsoluteFill style={{
          transformOrigin: '0 0',
          transform: `translate(${canvas.width / 2}px, ${canvas.height / 2}px) rotate(${cam.rotation}deg) `
            + `scale(${cam.zoom}) translate(${-cam.x}px, ${-cam.y}px)`,
        }}>
          {world}
        </AbsoluteFill>
        {captions ? <Captions speech={render.speech} frame={frame} canvas={canvas} /> : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
