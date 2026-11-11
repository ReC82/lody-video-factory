import React from 'react';
import {Composition} from 'remotion';
import {LodyScene} from './LodyScene.jsx';

// Les dimensions et la durée viennent de l'entrée de scène (props), jamais de valeurs codées en dur.
const fromProps = ({props}) => ({
  durationInFrames: props.scene.duration_frames,
  fps: props.canvas.fps,
  width: props.canvas.width,
  height: props.canvas.height,
});

const EMPTY = {
  canvas: {fps: 30, width: 1080, height: 1920},
  scene: {id: 'vide', duration_frames: 1, render: {layers: [], fallback: {type: 'still', frame: 0}}},
  assets: {},
  envelope: null,
  captions: true,
};

export const RemotionRoot = () => (
  <Composition id="LodyScene" component={LodyScene} durationInFrames={1} fps={30} width={1080} height={1920}
    defaultProps={EMPTY} calculateMetadata={fromProps} />
);
