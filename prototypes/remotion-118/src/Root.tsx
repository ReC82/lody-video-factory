import React from 'react';
import { Composition } from 'remotion';
import { Scene } from './Scene';
import { DURATION_IN_FRAMES, FPS, HEIGHT, WIDTH } from './timeline.mjs';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="LodyProto118"
    component={Scene}
    durationInFrames={DURATION_IN_FRAMES}
    fps={FPS}
    width={WIDTH}
    height={HEIGHT}
  />
);
