// Renderiza vários gráficos Remotion num único processo (bundle uma vez só).
// Uso: node render.mjs jobs.json
// jobs.json = {"concurrency":2,"jobs":[{"id":"HookTitle","props":{...,"durationInFrames":75},"outDir":"/abs/dir"}]}
// Saída: sequência PNG RGBA (frame-000.png ...) com transparência — o compositor Python
// (scripts/vfx.py) lê os PNGs direto, sem conversão YUV (sem desvio de cor).
// Com "mp4": "/abs/x.mp4" renderiza também um H.264 opaco (prévia).
import {bundle} from '@remotion/bundler';
import {renderFrames, renderMedia, selectComposition} from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
// Chromium local (rede pode bloquear o download do chrome-headless-shell do Remotion). Ordem:
// 1) env REMOTION_BROWSER_EXECUTABLE; 2) arquivo browser-executable.txt ao lado deste script (escrito por
// `preflight.py --fix`, fora do git); 3) null = o Remotion baixa o próprio Chrome (costuma falhar aqui).
function pickBrowser() {
  if (process.env.REMOTION_BROWSER_EXECUTABLE) return process.env.REMOTION_BROWSER_EXECUTABLE;
  const f = path.join(here, 'browser-executable.txt');
  if (fs.existsSync(f)) {
    const p = fs.readFileSync(f, 'utf8').trim();
    if (p && fs.existsSync(p)) return p;
  }
  return null;
}
const browserExecutable = pickBrowser();
if (browserExecutable) console.log(`browser: ${browserExecutable}`);
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const t0 = Date.now();
const serveUrl = await bundle({entryPoint: path.join(here, 'src', 'index.jsx'), publicDir: path.join(here, 'public')});
console.log(`bundle ok em ${((Date.now() - t0) / 1000).toFixed(1)} s`);
for (const job of spec.jobs) {
  const t1 = Date.now();
  const comp = await selectComposition({serveUrl, id: job.id, inputProps: job.props, logLevel: 'error', browserExecutable});
  fs.mkdirSync(job.outDir, {recursive: true});
  await renderFrames({
    composition: comp, serveUrl, inputProps: job.props, outputDir: job.outDir,
    imageFormat: 'png', concurrency: spec.concurrency || 2, browserExecutable,
    chromiumOptions: {gl: 'swangle'}, logLevel: 'error',
    onStart: () => {}, onFrameUpdate: () => {},
    imageSequencePattern: 'frame-[frame].[ext]',
  });
  if (job.mp4) {
    await renderMedia({composition: comp, serveUrl, inputProps: job.props, outputLocation: job.mp4,
      codec: 'h264', crf: 18, concurrency: spec.concurrency || 2, browserExecutable, chromiumOptions: {gl: 'swangle'}, logLevel: 'error'});
  }
  fs.writeFileSync(path.join(job.outDir, 'done.json'), JSON.stringify({frames: comp.durationInFrames, id: job.id}));
  console.log(`✓ ${job.id} -> ${job.outDir} (${comp.durationInFrames} q, ${((Date.now() - t1) / 1000).toFixed(1)} s)`);
}
