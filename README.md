# editinstagraam

Skills do Claude para edição de vídeos do Instagram (Reels). Cada skill vive na sua própria pasta, para que outras
skills de edição possam ser acrescentadas no futuro.

| Pasta | O que faz |
|---|---|
| [`reel-polish/`](reel-polish/) | Acabamento profissional de vídeo falado de celular: reenquadramento 9:16 seguindo o rosto, estabilização, correção de cor com proteção da pele, looks, retoque de pele, retoque de aparência (pele, ponta do nariz, entradas do cabelo) e polimento da voz — sem alterar a timeline. Gera um `polished.mp4` 1080×1920 que alimenta a skill `reel-editor`. |
| [`reel-banco/`](reel-banco/) | Banco de material pesquisável para Reels: ingere vídeos longos, PDFs e artefatos HTML da pasta `01_entrada` do Drive (transcrição no formato da reel-editor, cenas, miniaturas, segmentos com tema/tese/citação/gancho/público) e busca por BM25; recorta trechos como clipes/B-roll para a reel-director. Só o índice leve (`reel-banco/index/`) vai para o git; a mídia fica no Drive. |

Este repositório é a **fonte da verdade** da skill. O `SKILL.md` salvo na conta do Claude é idêntico a
[`reel-polish/SKILL.md`](reel-polish/SKILL.md) e, no "Passo 0", clona/atualiza este repositório em
`~/reel-polish-src` e usa `~/reel-polish-src/reel-polish/` como pasta da skill.

A `reel-banco` usa o mesmo clone (`~/reel-polish-src/reel-banco/`); instruções em
[`reel-banco/SKILL.md`](reel-banco/SKILL.md) e [`reel-banco/README.md`](reel-banco/README.md).

## Checagem prévia (antes de qualquer Reel)

`python3 preflight.py --fix` (raiz do repositório, só biblioteca padrão, 1–2 min) confere de uma vez tudo o que
costumava travar no meio do trabalho — links do Drive, scripts e `node_modules` da reel-director, Chromium local,
chave Fastcloner, reel-polish/reel-editor, modelos, ffmpeg, whisper, disco e CPUs —, corrige o que é local e
imprime a tabela de estimativa de tempo (`--estimar <bruto_s> <reel_s>`). Ver "Passo 0.5" nos `SKILL.md`.

## Instalação

```bash
git clone --depth 1 https://github.com/gabrielquixabeira96-tech/editinstagraam ~/reel-polish-src
cd ~/reel-polish-src/reel-polish
pip install -r requirements.txt        # numpy, scipy, opencv-python-headless, mediapipe
                                       # (em Linux gerenciado pelo sistema: --break-system-packages)
python scripts/00_check_env.py         # verifica Python, ffmpeg e filtros
python tests/test_pipeline.py          # teste de ponta a ponta com vídeo sintético
```

- **ffmpeg** é obrigatório. Linux: `sudo apt install ffmpeg`. macOS: `brew install ffmpeg-full` (ver
  [`reel-polish/README.md`](reel-polish/README.md) para as variáveis `REEL_FFMPEG`/`REEL_FFPROBE`).
- Python 3.10+.
- Os modelos pequenos (YuNet, Haar, RNNoise) estão em `reel-polish/models/` (licenças em
  [`models/LICENSES.md`](reel-polish/models/LICENSES.md)). Os modelos do MediaPipe (~20 MB) **não** são versionados:
  `05b_retouch.py` os baixa sozinho na primeira execução.

## Preset de aparência (Gabriel)

Quando o vídeo mostra o dono da conta, o `polish.json` recebe sempre este bloco de retoque, aprovado por ele:

```json
"retouch": {"enabled": true, "nose_tip_reduce": 0.22, "hairline": 0.85, "beard": 0.0, "skin_texture": 0.6,
            "hairline_shape": false, "beard_shape": false, "hairline_lower_max": 0.08, "hairline_recession": 0.04,
            "protect_bottom_from": 0.6, "landmark_smooth_frames": 7}
```

- Pele: uniformização de tom preservando textura (`skin_texture` 0,6).
- Nariz: redução sutil da ponta (`nose_tip_reduce` 0,22).
- Entradas: só escurece o couro cabeludo entre fios existentes, com grão casado e sem linha geométrica.
- Barba: **nunca** é editada (`beard` 0, `beard_shape` false).
- `protect_bottom_from` 0,6 preserva a faixa da legenda em vídeos já editados.

Para aplicar só o retoque num vídeo já pronto:

```bash
python scripts/05b_retouch.py polish.json --in polished.mp4 --out retouched.mp4
```

Detalhes de uso, regras de decisão e lições aprendidas: [`reel-polish/SKILL.md`](reel-polish/SKILL.md).

## Versões

- `reel-polish-v3` — motor de retoque `05b_retouch.py` (MediaPipe) com preset de aparência e lições registradas.
