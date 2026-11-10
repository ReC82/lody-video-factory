// Scène du prototype #118 : tout est dessiné en SVG et recalculé à CHAQUE image à partir de
// `useCurrentFrame()` — aucune image matricielle, aucun zoom/pan d'image fixe.
// Personnage fictif « Mimo » (créature ronde à oreilles) : ce n'est PAS Eli.
import React from 'react';
import { AbsoluteFill, Audio, interpolateColors, spring, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import envelope from './envelope.json';
import {
  BAR_TARGETS,
  HEIGHT,
  WIDTH,
  WORDS,
  armLeft,
  armRight,
  barHeight,
  blink,
  breathe,
  cloudX,
  currentWordIndex,
  gaze,
  grassSway,
  headBob,
  keyframes,
  mouthOpen,
  phraseBounds,
  progress,
  sun,
  titleLetters,
  wingFlap,
} from './timeline.mjs';

const FONT = '"DejaVu Sans", Arial, sans-serif';
const BODY = '#2bb3a3';
const BODY_DARK = '#1d8c80';
const BELLY = '#bff0e6';
const TITLE = 'Mimo s’anime !';

const Sky: React.FC<{ frame: number }> = ({ frame }) => {
  const top = interpolateColors(frame, [0, 200, 450], ['#ffa77a', '#f59ac0', '#6fb8f0']);
  const bottom = interpolateColors(frame, [0, 200, 450], ['#ffe2b8', '#ffd9e6', '#d8f1ff']);
  const s = sun(frame);
  return (
    <g>
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={top} />
          <stop offset="1" stopColor={bottom} />
        </linearGradient>
      </defs>
      <rect width={WIDTH} height={HEIGHT} fill="url(#sky)" />
      <g transform={`translate(220 ${s.y})`}>
        <g transform={`rotate(${s.rays})`}>
          {Array.from({ length: 12 }, (_, i) => (
            <rect key={i} x={-8} y={-190} width={16} height={60} rx={8} fill="#ffd34d" opacity={0.85} transform={`rotate(${i * 30})`} />
          ))}
        </g>
        <circle r={110} fill="#ffcf3f" />
        <circle r={110} fill="#fff3a8" opacity={0.25 + 0.15 * Math.sin(frame * 0.2)} />
      </g>
    </g>
  );
};

const Cloud: React.FC<{ x: number; y: number; s: number }> = ({ x, y, s }) => (
  <g transform={`translate(${x} ${y}) scale(${s})`} fill="#ffffff" opacity={0.92}>
    <ellipse cx={0} cy={0} rx={110} ry={48} />
    <circle cx={-50} cy={-30} r={52} />
    <circle cx={30} cy={-50} r={66} />
    <circle cx={90} cy={-12} r={44} />
  </g>
);

const Bird: React.FC<{ x: number; y: number; flap: number }> = ({ x, y, flap }) => (
  <path
    d={`M ${x - 34} ${y} Q ${x - 17} ${y - 26 * flap} ${x} ${y} Q ${x + 17} ${y - 26 * flap} ${x + 34} ${y}`}
    stroke="#2b3a55"
    strokeWidth={6}
    strokeLinecap="round"
    fill="none"
  />
);

const Landscape: React.FC<{ frame: number }> = ({ frame }) => {
  const far = 25 * Math.sin(frame * 0.012);
  const near = 45 * Math.sin(frame * 0.012 + 0.6);
  return (
    <g>
      <path transform={`translate(${far} 0)`} d={`M -100 1380 Q 200 1180 520 1330 T 1200 1300 L 1200 ${HEIGHT} L -100 ${HEIGHT} Z`} fill="#8fd18a" />
      <path transform={`translate(${near} 0)`} d={`M -100 1520 Q 300 1400 640 1500 T 1250 1460 L 1250 ${HEIGHT} L -100 ${HEIGHT} Z`} fill="#5fb35a" />
      <rect y={1680} width={WIDTH} height={HEIGHT - 1680} fill="#4a9a46" />
      {Array.from({ length: 44 }, (_, i) => {
        const x = 12 + i * 24.5;
        const h = 70 + ((i * 37) % 45);
        return (
          <path
            key={i}
            d={`M ${x - 7} 1700 Q ${x} ${1700 - h * 0.6} ${x} ${1700 - h} Q ${x + 3} ${1700 - h * 0.6} ${x + 7} 1700 Z`}
            fill={i % 3 === 0 ? '#3d8a3a' : '#69c063'}
            transform={`rotate(${grassSway(frame, i)} ${x} 1700)`}
          />
        );
      })}
    </g>
  );
};

const Arm: React.FC<{ x: number; y: number; shoulder: number; elbow: number; mirror?: boolean }> = ({ x, y, shoulder, elbow, mirror }) => (
  <g transform={`translate(${x} ${y}) scale(${mirror ? -1 : 1} 1) rotate(${shoulder})`}>
    <rect x={-22} y={0} width={44} height={140} rx={22} fill={BODY_DARK} />
    <g transform={`translate(0 125) rotate(${elbow})`}>
      <rect x={-20} y={0} width={40} height={120} rx={20} fill={BODY_DARK} />
      <circle cx={0} cy={128} r={34} fill={BODY} />
    </g>
  </g>
);

const Eye: React.FC<{ cx: number; cy: number; id: string; frame: number }> = ({ cx, cy, id, frame }) => {
  const g = gaze(frame);
  const lid = blink(frame);
  const rx = 50;
  const ry = 60;
  return (
    <g>
      <defs>
        <clipPath id={id}>
          <ellipse cx={cx} cy={cy} rx={rx} ry={ry} />
        </clipPath>
      </defs>
      <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="#ffffff" />
      <g clipPath={`url(#${id})`}>
        <circle cx={cx + g.x * 22} cy={cy + g.y * 24} r={26} fill="#1f2a44" />
        <circle cx={cx + g.x * 22 + 9} cy={cy + g.y * 24 - 10} r={8} fill="#ffffff" />
        <rect x={cx - rx - 2} y={cy - ry - 2} width={2 * rx + 4} height={(2 * ry + 4) * lid} fill={BODY} />
        {lid > 0.05 ? (
          <line x1={cx - rx} x2={cx + rx} y1={cy - ry - 2 + (2 * ry + 4) * lid} y2={cy - ry - 2 + (2 * ry + 4) * lid} stroke={BODY_DARK} strokeWidth={6} />
        ) : null}
      </g>
      <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="none" stroke={BODY_DARK} strokeWidth={6} />
    </g>
  );
};

const Mouth: React.FC<{ open: number }> = ({ open }) => {
  const cx = 540;
  const cy = 1105;
  if (open < 0.04) {
    return <path d={`M ${cx - 42} ${cy - 6} Q ${cx} ${cy + 26} ${cx + 42} ${cy - 6}`} stroke="#4a1f2a" strokeWidth={9} strokeLinecap="round" fill="none" />;
  }
  const rx = 46 - 12 * open;
  const ry = 8 + 46 * open;
  return (
    <g>
      <defs>
        <clipPath id="mouth">
          <ellipse cx={cx} cy={cy + ry * 0.3} rx={rx} ry={ry} />
        </clipPath>
      </defs>
      <ellipse cx={cx} cy={cy + ry * 0.3} rx={rx} ry={ry} fill="#5a1d2b" />
      <g clipPath="url(#mouth)">
        <ellipse cx={cx} cy={cy + ry * 1.15} rx={rx * 0.8} ry={ry * 0.55} fill="#ef6f86" />
        <rect x={cx - rx} y={cy + ry * 0.3 - ry} width={2 * rx} height={ry * 0.28} fill="#ffffff" />
      </g>
    </g>
  );
};

const Character: React.FC<{ frame: number }> = ({ frame }) => {
  const open = mouthOpen(frame, envelope as number[]);
  const bob = headBob(frame, open);
  const tilt = 3 * Math.sin(frame * 0.05) + keyframes(frame, [[148, 0], [160, 5], [236, 5], [246, 0]]);
  const right = armRight(frame);
  const left = armLeft(frame);
  return (
    <g>
      <ellipse cx={540} cy={1712} rx={210 - 8 * Math.sin(frame * 0.14)} ry={26} fill="#2f6b2c" opacity={0.35} />
      {/* corps qui respire (mise à l'échelle verticale autour des pieds) */}
      <g transform={`translate(540 1700) scale(1 ${breathe(frame)}) translate(-540 -1700)`}>
        <Arm x={420} y={1250} shoulder={left.shoulder} elbow={left.elbow} mirror />
        <Arm x={660} y={1250} shoulder={right.shoulder} elbow={right.elbow} />
        <ellipse cx={500} cy={1680} rx={56} ry={30} fill={BODY_DARK} />
        <ellipse cx={580} cy={1680} rx={56} ry={30} fill={BODY_DARK} />
        <ellipse cx={540} cy={1440} rx={170} ry={235} fill={BODY} />
        <ellipse cx={540} cy={1475} rx={110} ry={160} fill={BELLY} />
      </g>
      {/* tête : hochement pendant la parole et inclinaison vers le graphique */}
      <g transform={`translate(0 ${bob}) rotate(${tilt} 540 1180)`}>
        <path d="M 400 900 L 380 720 L 500 840 Z" fill={BODY_DARK} transform={`rotate(${4 * Math.sin(frame * 0.3)} 440 860)`} />
        <path d="M 680 900 L 700 720 L 580 840 Z" fill={BODY_DARK} transform={`rotate(${-4 * Math.sin(frame * 0.3 + 1)} 640 860)`} />
        <circle cx={540} cy={1020} r={205} fill={BODY} />
        <ellipse cx={440} cy={1085} rx={34} ry={20} fill="#ff8fa3" opacity={0.8} />
        <ellipse cx={640} cy={1085} rx={34} ry={20} fill="#ff8fa3" opacity={0.8} />
        <Eye cx={465} cy={985} id="eyeL" frame={frame} />
        <Eye cx={615} cy={985} id="eyeR" frame={frame} />
        <Mouth open={open} />
      </g>
    </g>
  );
};

const Chart: React.FC<{ frame: number }> = ({ frame }) => {
  const slide = keyframes(frame, [[140, 520], [165, 0]]);
  const p = progress(frame);
  const circ = 2 * Math.PI * 46;
  return (
    <g transform={`translate(${600 + slide} 470)`}>
      <rect width={420} height={380} rx={34} fill="#ffffff" opacity={0.97} />
      <text x={30} y={60} fontFamily={FONT} fontSize={34} fontWeight={700} fill="#1f2a44">
        Énergie
      </text>
      {BAR_TARGETS.map((_, i) => {
        const h = 230 * barHeight(frame, i);
        return <rect key={i} x={40 + i * 70} y={340 - h} width={46} height={h} rx={10} fill={['#ff7a59', '#2bb3a3', '#ffc542', '#6c63ff'][i]} />;
      })}
      <g transform="translate(350 70)">
        <circle r={46} fill="none" stroke="#e3e8f0" strokeWidth={12} />
        <circle r={46} fill="none" stroke="#6c63ff" strokeWidth={12} strokeLinecap="round" strokeDasharray={`${circ * p} ${circ}`} transform="rotate(-90)" />
        <text y={10} textAnchor="middle" fontFamily={FONT} fontSize={26} fontWeight={700} fill="#1f2a44">
          {Math.round(p * 100)}
        </text>
      </g>
    </g>
  );
};

const Title: React.FC<{ frame: number; fps: number }> = ({ frame, fps }) => {
  const n = titleLetters(frame, TITLE);
  const letters = Array.from(TITLE);
  const wobble = 2 * Math.sin(frame * 0.08);
  return (
    <g transform={`translate(540 230) rotate(${wobble})`}>
      <text textAnchor="middle" fontFamily={FONT} fontSize={104} fontWeight={800} fill="#1f2a44" stroke="#ffffff" strokeWidth={14} paintOrder="stroke">
        {letters.map((ch, i) => {
          const sp = spring({ frame: frame - (6 + i * 2), fps, config: { damping: 11, stiffness: 160 } });
          return (
            <tspan key={i} opacity={i < n ? 1 : 0} fontSize={104 * (0.4 + 0.6 * sp)}>
              {ch}
            </tspan>
          );
        })}
      </text>
      <text y={80} textAnchor="middle" fontFamily={FONT} fontSize={40} fill="#1f2a44" opacity={keyframes(frame, [[30, 0], [50, 1]])}>
        Prototype 2D · Remotion · #118
      </text>
    </g>
  );
};

const Subtitles: React.FC<{ frame: number }> = ({ frame }) => {
  const idx = currentWordIndex(frame);
  const phrase = idx >= 0 ? WORDS[idx].phrase : [0, 1, 2].find((p) => frame >= phraseBounds(p)[0] - 6 && frame < phraseBounds(p)[1] + 24);
  if (phrase === undefined) return null;
  const [a, b] = phraseBounds(phrase);
  const fade = Math.min(keyframes(frame, [[a - 6, 0], [a, 1]]), keyframes(frame, [[b + 12, 1], [b + 24, 0]]));
  const words = WORDS.filter((w) => w.phrase === phrase);
  return (
    <g opacity={fade} transform={`translate(0 ${(1 - fade) * 30})`}>
      <rect x={40} y={1760} width={1000} height={110} rx={30} fill="#1f2a44" opacity={0.78} />
      <text x={540} y={1832} textAnchor="middle" fontFamily={FONT} fontSize={42} fontWeight={700}>
        {words.map((w) => {
          const active = WORDS.indexOf(w) === idx;
          const said = frame >= w.start;
          return (
            <tspan key={`${w.start}-${w.text}`} fill={active ? '#ffd34d' : said ? '#ffffff' : '#8d97ad'}>
              {w.text + ' '}
            </tspan>
          );
        })}
      </text>
    </g>
  );
};

export const Scene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const birdA = { x: keyframes(frame, [[200, 1200], [330, -150]], (t) => t), y: 640 + 20 * Math.sin(frame * 0.2) };
  return (
    <AbsoluteFill style={{ backgroundColor: '#7cc6f2' }}>
      <Audio src={staticFile('voice.wav')} />
      <svg width={WIDTH} height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
        <Sky frame={frame} />
        <Cloud x={cloudX(frame, 1.6, 100)} y={420} s={1} />
        <Cloud x={cloudX(frame, 1.0, 900)} y={760} s={0.7} />
        <Cloud x={cloudX(frame, 2.4, 500)} y={1040} s={1.25} />
        <Bird x={birdA.x} y={birdA.y} flap={wingFlap(frame)} />
        <Bird x={cloudX(frame, 3.2, 300)} y={360 + 15 * Math.sin(frame * 0.15)} flap={wingFlap(frame, 1.3)} />
        <Landscape frame={frame} />
        <Chart frame={frame} />
        <Character frame={frame} />
        <Title frame={frame} fps={fps} />
        <Subtitles frame={frame} />
      </svg>
    </AbsoluteFill>
  );
};
