import { Config } from '@remotion/cli/config';

// Rendu sobre pour un hôte partagé (2 CPU, ~1,8 Go) : une seule page Chrome à la fois.
Config.setConcurrency(1);
Config.setVideoImageFormat('jpeg');
Config.setJpegQuality(90);
Config.setCodec('h264');
Config.setCrf(26);
Config.setPixelFormat('yuv420p');
Config.setOverwriteOutput(true);
