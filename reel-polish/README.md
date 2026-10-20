# reel-polish

Skill do Claude que dá **acabamento profissional** a um vídeo falado de celular antes (ou depois) da edição
pela skill `reel-editor`: reenquadramento 9:16 seguindo o rosto, estabilização, correção de cor de colorista
(exposição, balanço de branco, saturação com proteção da pele, looks), retoque de pele invisível, polimento
da voz e nitidez final — **sem alterar a timeline** (mesma duração, mesmo fps, áudio em sincronia exata).

Saída: `work/<nome>/polish/polished.mp4` — 1080×1920, 30 fps, H.264 CRF 17, AAC 192 kbps — pronto para ser o
`source` da `reel-editor` (que transcreve, corta, legenda, mixa música e normaliza a −14 LUFS).

O Claude lê `analysis.txt` e decide (`polish.json`); scripts fixos e testados executam.

## Instalação (macOS)

```bash
# 1. ffmpeg completo (com vid.stab e arnndn; o ffmpeg-full também serve para a reel-editor, que precisa de libass)
brew install ffmpeg-full
echo 'export REEL_FFMPEG="$(brew --prefix ffmpeg-full)/bin/ffmpeg"'   >> ~/.zshrc
echo 'export REEL_FFPROBE="$(brew --prefix ffmpeg-full)/bin/ffprobe"' >> ~/.zshrc
source ~/.zshrc
# (sem as variáveis, os scripts procuram o ffmpeg-full em /opt/homebrew/opt/ffmpeg-full/bin)

# 2. Python 3.11+ no mesmo venv da reel-editor
brew install python@3.12
python3.12 -m venv ~/.venvs/reel && source ~/.venvs/reel/bin/activate
pip install -r requirements.txt        # numpy, scipy, opencv-python-headless

# 3. Verificar
python scripts/00_check_env.py
```

Linux: `sudo apt install ffmpeg` (Ubuntu/Debian já trazem vid.stab e arnndn) + `pip install -r requirements.txt`.

Ordem de busca do ffmpeg: `$REEL_FFMPEG` → ffmpeg-full do Homebrew → `ffmpeg` do PATH → `imageio-ffmpeg`.
Filtros opcionais: sem `vidstabdetect/vidstabtransform` a estabilização é pulada (com aviso); sem `arnndn` a
redução de ruído usa `afftdn`; sem `lut3d` a LUT `.cube` não é aplicada.

Modelos incluídos (sem download): `models/face_detection_yunet_2023mar.onnx` (detector de rosto YuNet do
OpenCV Zoo, Apache-2.0), `models/haarcascade_frontalface_default.xml` (reserva, BSD/OpenCV) e
`models/rnnoise_bd.rnnn` (modelo RNNoise "beguiling-drafter" para o filtro `arnndn`; domínio público).

## Uso com o Claude

Envie o vídeo e peça "deixa mais bonito / reenquadra no meu rosto / limpa o áudio". O Claude:
1. analisa (`01_analyze.py`) e lê `analysis.txt` + `analysis_sheet.png`;
2. escreve `polish.json` (enquadramento, estabilização, look, exposição, WB, retoque, áudio);
3. **mostra as decisões e a folha de análise e espera seu "ok"**;
4. roda `run_all.py`, confere `qa_report.json` e `compare_sheet.png`;
5. entrega `polished.mp4` e, se você pedir a edição, roda a `reel-editor` com `source = polished.mp4`.

## Uso manual

```bash
source ~/.venvs/reel/bin/activate
python scripts/01_analyze.py ~/Movies/bruto.mov                 # -> ~/Movies/work/bruto/polish/analysis.txt
# escreva ~/Movies/work/bruto/polish/polish.json (formato no SKILL.md; o analysis.txt traz um rascunho)
python scripts/run_all.py ~/Movies/work/bruto/polish/polish.json # -> polished.mp4 + qa_report.json + compare_sheet.png
```

Depois, na reel-editor: `"source": "polish/polished.mp4"` na EDL (ou aponte a transcrição/EDL para o polido).

## Pipeline

| Script | Faz | Saída |
|--------|-----|-------|
| `00_check_env.py` | ffmpeg + filtros, Python, numpy/scipy/OpenCV, modelos, looks | relatório |
| `01_analyze.py` | rosto por quadro (YuNet, 5 fps) rastreado e suavizado (Savitzky-Golay); exposição, WB (neutros e pele), nitidez, ruído, tremor (correlação de fase), áudio (piso, SNR, sibilância) | `analysis.json/.txt`, `analysis_sheet.png` |
| `02_reframe.py` | recorte 9:16 dinâmico: olhos no terço superior, zona morta + easing, zoom suavizado, limites do quadro, Lanczos + unsharp na ampliação; `fit` cover/smart/contain, `--static` | `reframe.mkv`, `track.json` |
| `03_stabilize.py` | vid.stab 2 passagens (moderado, `optzoom`), opcional/auto pelo tremor | `stabilized.mkv` |
| `04_grade.py` | hqdn3d → curva de tons (níveis, gama p/ pele, S suave, ombro) → colortemperature/tint → huesaturation (sem r/y) + vibrance → tintas do look → LUT → vinheta | `graded.mkv` |
| `05b_retouch.py` | retoque avançado opcional (MediaPipe Face Landmarker 478 pts + selfie multiclass): warp "pinch" na ponta do nariz, preenchimento de entradas/falhas do cabelo e da barba (escurecer + textura clonada), separação de frequências da pele; landmarks suavizados no tempo; `--preview`, `--clip`, `--suggest` | `retouch.mkv` (ou .mp4 com áudio copiado) |
| `05_beauty.py` | máscara de pele (YCrCb × elipse do rosto × bordas − olhos − boca), separação de frequências com bilateral de baixa intensidade, sob-olhos, brilho nos olhos, anti-brilho; `--intensity` 0–100 | `beauty.mkv` |
| `06_audio_polish.py` | highpass → arnndn/afftdn → deesser → EQ (250 Hz −2, 4 kHz +2, 10 kHz +1,5) → compressor 2,5:1 → limiter; latência medida e compensada | `audio_polished.wav` |
| `07_render.py` | unsharp luma final + fade opcional; H.264 CRF 17, AAC 192k | `polished.mp4` |
| `08_qa.py` | duração/quadros, formato, enquadramento (≥ 95% na zona: olhos 18–55% da altura, centro x 28–72%), clipping, nitidez, sincronia (±1,5 ms), textura da pele (≥ 60%) | `qa_report.json`, `compare_sheet.png` |
| `run_all.py` | encadeia com cache por etapa (`state.json`), `--from`, `--force` | — |

A nitidez final é aplicada no render, depois do retoque, para não realçar o que o beauty suavizou.

### O que o cache refaz

| Você mudou… | Etapas refeitas |
|---|---|
| `framing`, `fit`, `static` | reframe e tudo depois |
| `stabilize` | stabilize → grade → beauty → render → QA |
| `look`, `lut`, `exposure`, `white_balance`, `denoise`, saturação/vinheta | grade → beauty → render → QA |
| `beauty_intensity`, `beauty.*` | beauty → retouch → render → QA |
| `retouch.*` | retouch → render → QA |
| `audio.*` | audio → render → QA |
| `sharpen`, `fade` | render → QA |

## Testes

```bash
python tests/test_pipeline.py          # vídeo sintético (rosto desenhado + áudio) — mode raw, cache, mode final; exige QA aprovado
python tests/test_pipeline.py --keep   # mantém a pasta temporária para inspecionar
```

## Desempenho (honesto)

Em CPU de notebook comum: análise ~0,7× a duração do vídeo; reframe ~3–4×; grade ~0,5–1×; beauty ~2–3×;
render ~2–4×. Um bruto de 3 min leva ~25–40 min no total; a partir da segunda rodada, o cache refaz só o
necessário. Os `.mkv` intermediários (CRF 12) ocupam ~25 MB por segundo de vídeo — apague após a entrega.

## Limitações

- Ampliar um recorte de vídeo 1080p horizontal até 1080×1920 (≈1,8×) custa nitidez; fica bom no celular, mas
  um bruto vertical/4K sempre será melhor. O QA relaxa a exigência de nitidez conforme a ampliação.
- O rastreamento segue um rosto (o maior/mais contínuo); mãos na frente do rosto e perfil total podem perder a
  detecção por instantes (a trajetória é interpolada e suavizada, então não há saltos).
- Retoque conservador: evita pele "plástica" por desenho (QA mede a textura), por isso não apaga olheiras profundas
  nem acne severa.
- Estabilização forte sobre um recorte já ampliado reduz ainda mais a nitidez — por isso só entra quando há tremor.
- Em `mode final` (vídeo já com legenda e música), o polimento de áudio age sobre a mixagem inteira; use com parcimônia.

## Estrutura

```
reel-polish/
  SKILL.md  README.md  requirements.txt
  scripts/  00_check_env.py 01_analyze.py 02_reframe.py 03_stabilize.py 04_grade.py 05_beauty.py
            06_audio_polish.py 07_render.py 08_qa.py run_all.py polish_common.py
  looks/    natural.json cinematic.json warm.json clean.json
  models/   face_detection_yunet_2023mar.onnx haarcascade_frontalface_default.xml rnnoise_bd.rnnn LICENSES.md
  tests/    test_pipeline.py
```
