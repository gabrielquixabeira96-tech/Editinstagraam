---
name: reel-polish
description: Passe de acabamento profissional ("editor de Hollywood + colorista + retoque") para vídeos falados de celular — reenquadra em 9:16 seguindo o rosto (resolve rosto pequeno e vídeo horizontal), estabiliza, corrige exposição e cor com proteção do tom de pele, aplica um look (natural, cinematic, warm, clean), retoque de pele invisível, limpa e dá corpo à voz — tudo SEM alterar a timeline, gerando um polished.mp4 1080×1920 que alimenta a skill reel-editor. Use SEMPRE que o usuário pedir "deixa mais bonito", "melhora a imagem/cor/pele", "acabamento profissional", "cara de Hollywood/cinema", "reenquadra no meu rosto", "estabiliza", "limpa o áudio", ou reclamar de rosto pequeno, vídeo horizontal/deitado, cor lavada/azulada, pele, ruído de fundo — mesmo que ele não diga "polir". Também quando um Reel já editado pela reel-editor "ficou feio" ou "sem vida". Inclui também retoque de aparência com MediaPipe (textura da pele, redução sutil da ponta do nariz, preenchimento invisível das entradas do cabelo) — use quando ele pedir para "melhorar a pele", "afinar o nariz", "arrumar a entrada do cabelo" ou "me deixar mais bonito"; quando o vídeo mostra o próprio Gabriel (dono da conta), aplique sempre o preset de aparência dele.
---

# reel-polish

## Passo 0 — instalar/atualizar a partir do GitHub (SEMPRE, antes de qualquer outro comando)

A fonte da verdade desta skill é o repositório `gabrielquixabeira96-tech/editinstagraam` (pasta `reel-polish/`),
não a pasta sincronizada da conta (que só contém este SKILL.md e pode estar desatualizada).

```bash
if [ ! -f ~/reel-polish-src/reel-polish/scripts/05b_retouch.py ]; then
  git clone --depth 1 https://github.com/gabrielquixabeira96-tech/editinstagraam ~/reel-polish-src
else
  git -C ~/reel-polish-src pull
fi
pip install --break-system-packages -r ~/reel-polish-src/reel-polish/requirements.txt
```

- A partir daqui, a **pasta da skill é `~/reel-polish-src/reel-polish/`**: rode todos os comandos de dentro dela
  (`cd ~/reel-polish-src/reel-polish`) ou com caminhos absolutos para ela — NUNCA a pasta sincronizada.
- Também é preciso `ffmpeg` no PATH (Linux: `apt install ffmpeg`; macOS: ver README do repositório).
- Se o clone falhar por falta de acesso ao GitHub, peça ao usuário para anexar o repositório
  `gabrielquixabeira96-tech/editinstagraam` à sessão e tente de novo.
- Os modelos grandes do MediaPipe (`models/face_landmarker.task`, `models/selfie_multiclass_256x256.tflite`)
  não estão no repositório: `05b_retouch.py` os baixa sozinho na 1ª execução.

**Princípio (o mesmo da reel-editor):** você (Claude) DECIDE lendo `analysis.txt` e escreve `polish.json`;
os SCRIPTS EXECUTAM de forma determinística. Nunca improvise comandos ffmpeg para um vídeo específico —
se faltar um recurso, ele vira opção de um script.

**Regra de ouro:** esta skill **nunca altera a timeline** — sem cortes, sem mudança de duração, fps ou
deslocamento de áudio. Por isso `transcript.json` e `edl.json` da reel-editor feitos sobre o bruto
continuam válidos sobre o `polished.mp4`. Esta skill NÃO transcreve, não corta, não legenda, não mixa
música nem normaliza loudness: isso é trabalho da reel-editor.

Caminhos abaixo são relativos à pasta da skill (`~/reel-polish-src/reel-polish/`, ver Passo 0). Todos os scripts têm `--help`.
Pasta de trabalho: `work/<nome-do-video>/polish/` ao lado do vídeo (dentro do mesmo `work/<nome>/`
que a reel-editor usa).

## Fluxo recomendado

```
bruto (qualquer proporção) ──reel-polish──▶ polished.mp4 (1080×1920, 30 fps, mesma duração)
                                                   └──reel-editor (source = polished.mp4)──▶ final.mp4
```

1. **Ambiente** (1ª vez na sessão): `python scripts/00_check_env.py`. Se reprovar, mostre o que falta
   (README: instalação macOS/Linux; precisa de ffmpeg com vid.stab/arnndn opcionais, Python 3.10+,
   numpy, scipy, opencv-python-headless).
2. **Análise:** `python scripts/01_analyze.py <video>` → `work/<nome>/polish/analysis.txt`, `analysis.json`,
   `analysis_sheet.png` (quadros amostrados com a caixa do rosto). Se já existir `analysis.json`, não refaça.
3. **Leia `analysis.txt`** — ele traz medidas (rosto, exposição, balanço de branco, nitidez, ruído, tremor,
   áudio), sugestões e um **rascunho de `polish.json`**. Olhe também `analysis_sheet.png`.
4. **Escreva `work/<nome>/polish/polish.json`** aplicando as regras de decisão abaixo (formato mais adiante).
5. **Aprovação (OBRIGATÓRIA):** mostre ao usuário um resumo das decisões (enquadramento e fit, estabilizar ou
   não, look, correção de exposição/cor, intensidade do retoque, tratamento de áudio, tempo estimado) **junto com
   `analysis_sheet.png`**, e espere um "ok" explícito. Ajustes pedidos aqui editam só o `polish.json`.
6. **Render:** `python scripts/run_all.py work/<nome>/polish/polish.json` (cache por etapa; refaz só o que mudou).
7. **QA:** leia `work/<nome>/polish/qa_report.json`. Se `"status": "reprovado"`, NÃO entregue: corrija a causa
   (quase sempre um campo do `polish.json` — veja "Ajustes posteriores") e rode de novo.
8. **Entrega:** `polished.mp4` + `compare_sheet.png` (antes × depois em 6 instantes) + resumo (rosto antes/depois em
   % da largura, correções aplicadas, avisos do QA). **Olhe o `compare_sheet.png` antes de entregar** — se a pele
   parecer "plástica", a cor exagerada ou o enquadramento cortar a cabeça, ajuste e re-renderize.
9. **Integração com a reel-editor:**
   - vídeo novo: rode a reel-editor normalmente com `"source"` da EDL apontando para `work/<nome>/polish/polished.mp4`
     (a transcrição pode ser feita no bruto ou no polido — a timeline é a mesma);
   - se já existe `work/<nome>/edl.json` e `transcript.json`: troque só o `"source"` da EDL para
     `polish/polished.mp4` e rode `run_all.py` da reel-editor (ela refaz corte → … → QA sem retranscrever).
     Como o polido já é 1080×1920, o `fit` da reel-editor vira `cover` automaticamente (sem faixa desfocada).

## Modos

- `"mode": "raw"` (padrão): entrada é o bruto → reenquadra + estabiliza + cor + pele + áudio + render.
- `"mode": "final"`: entrada é um `final.mp4` já editado (legenda queimada) → só cor, pele, nitidez e áudio
  (sem reenquadrar nem estabilizar). Como o áudio já tem música e loudness, use `"audio": {"enabled": false}`
  ou só `denoise_db` baixo; o resultado precisará de nova normalização se for repostado.
- **Retoque de aparência num vídeo já editado (com legenda queimada):** não rode o pipeline inteiro; aplique só o
  05b sobre o arquivo pronto, com `retouch.protect_bottom_from` cobrindo a faixa da legenda (ex.: 0,60):
  `python scripts/05b_retouch.py polish.json --in polished.mp4 --out retouched.mp4` (áudio copiado, timeline
  idêntica). Gere e **confira o compare/prévias** (`--preview`) antes de entregar.

## Regras de decisão (para montar o `polish.json`)

1. **Enquadramento.** `analysis.txt` diz quanto o rosto ocupa no recorte 9:16 de altura cheia ("cover") e quais
   enquadramentos cabem sem barras. `fit: auto` usa tela cheia (cover) se o rosto ficar ≤ 55% da largura; senão
   `smart` (recorte mais largo sobre fundo desfocado em cima/embaixo). Prefira **tela cheia** para talking head;
   use `framing: closeup` quando o usuário quer impacto/intimidade, `medium` como padrão, `wide` quando há
   gestos ou um produto na mão. `static: true` quando o rosto quase não se move (recorte fixo = zero risco
   de "câmera respirando"). Vídeo já vertical: o reenquadramento só aproxima/centraliza.
2. **Estabilizar** só se o tremor medido for > 0,25% da largura (`stabilize: "auto"` faz isso) ou se o usuário
   pedir. Vídeo em tripé/apoiado: `false` (estabilizar à toa custa nitidez e tempo).
3. **Look por ambiente:** carro/externa com céu → `natural` (e deixe o WB esquentar); fim de tarde/ambiente
   aconchegante → `warm`; consultório/fundo branco/jaleco → `clean`; conteúdo de impacto/storytelling →
   `cinematic` (teal-orange sutil, vinheta). Em dúvida, `natural`. Nunca empilhe LUT forte com `cinematic`.
4. **Exposição:** `exposure.auto` leva a luma do rosto a ~0,58 (70% da correção, sem "cara de HDR") e protege
   as altas luzes. Rosto muito escuro com fundo estourado (janela atrás): aceite — não dá para recuperar o fundo.
5. **Balanço de branco:** siga `suggested_temperature_shift` (positivo = esquentar). Se o usuário tem pele que
   já parece avermelhada, reduza para a metade. `strength` 0,7 é o padrão; 1,0 só quando o azul é evidente.
   **Atenção ao look `natural`:** o `vibrance` herdado (0,12) pode deixar a pele alaranjada e anular a correção de
   WB. Com pele já quente (R/B alto no rosto), use `"saturation": 1.0`, `"vibrance": -0.1`,
   `white_balance.strength: 1.0`, e **meça o R/B da pele antes e depois** (prévia do 04_grade) — o R/B tem de cair.
6. **Retoque (`beauty_intensity`):** 25–35 luz suave/boa pele; 35–50 luz dura (sol, LED de teto, flash) ou pele
   oleosa/manchada; nunca > 60 (perde textura — e o QA reprova pele "plástica"). Barba cerrada: o retoque só age na
   pele visível. Desligue `eye_brighten` se o usuário usa óculos com reflexo forte.
7. **Áudio:** `denoise_db` 18 para carro/rua/ar-condicionado (SNR < 20 dB), 12 padrão, 6 para ambiente silencioso;
   `deesser` 0,5 se a sibilância medida for alta. Não normalize aqui.
8. **Retoque avançado (`retouch`, MediaPipe):** quando o vídeo mostra o **Gabriel** (dono da conta), aplique SEMPRE o
   preset de aparência dele (`"retouch": {"preset": "gabriel"}` — seção "Preset de aparência — Gabriel", abaixo). Para outras pessoas, só quando pedirem
   explicitamente mudanças no próprio rosto — entradas na linha do cabelo, textura da pele, ponta do nariz. Liga com
   `"retouch": {"enabled": true, ...}`. Valores de partida: `nose_tip_reduce` 0,15 (0,20–0,25 = visível e ainda
   natural; nunca > 0,3), `hairline` 0,6–0,85 (só escurece o couro cabeludo entre fios existentes, com mechas, gradiente
   de densidade e grão casado), `skin_texture` 0,5 (0,7–0,8 = forte). **`beard` fica SEMPRE em 0** (ver "Lições").
   Em `mode final` ou retoque sobre vídeo com legenda queimada, defina `protect_bottom_from` (fração da altura a partir
   da qual nada é alterado, ex.: 0,60).
   `hairline_shape`/`beard_shape` (shape-up geométrico: linha-alvo do cabelo pelos terços faciais, linha da bochecha,
   contorno do pescoço, fios sintetizados) existem no código, mas **ficam `false`**: linha-alvo geométrica denuncia a
   edição. Só ligue `hairline_shape` se o usuário pedir explicitamente esse efeito; `beard_shape` nunca.
   Gere prévias antes de renderizar (`--preview`) e um trecho de 3 s (`--clip`) para checar estabilidade. Regra:
   o rosto precisa continuar inequivocamente a mesma pessoa — prefira sutil-e-convincente a forte-e-artificial.
   Requer `mediapipe` (já no requirements.txt; CPU); os modelos são baixados para `models/` na 1ª execução.
9. **Nitidez:** `sharpen` 0,6 padrão; 0,8 quando a ampliação do recorte for > 1,6× (o QA avisa); 0,4 com ruído alto.
10. **Honestidade:** se o rosto é detectado em < 70% dos quadros (mão na frente, perfil), avise que o rastreamento
   pode falhar nesses trechos e prefira `static: true`. Se o QA reprovar duas vezes pelo mesmo item, explique a
   limitação ao usuário em vez de forçar.

## Preset de aparência — Gabriel (aprovado pelo usuário)

Sempre que o vídeo mostra o Gabriel (dono da conta), use o preset nomeado `presets/gabriel.json` (aprovado em
2026-10-20, versão "v3"). No `polish.json` basta:

```json
"retouch": {"preset": "gabriel"}
```

Chaves escritas no bloco sobrescrevem as do preset (ex.: `{"preset": "gabriel", "protect_bottom_from": 0.70}`).
Conteúdo do preset (equivale a copiar o bloco inteiro):

```json
"retouch": {"enabled": true, "nose_tip_reduce": 0.26, "nose_alar_boost": 0.25, "hairline": 1.0,
            "hairline_band": 0.12, "skin_texture": 0.75, "beard": 0.0, "beard_shape": false,
            "hairline_shape": false, "hairline_lower_max": 0.08, "hairline_recession": 0.04,
            "protect_bottom_from": 0.6, "landmark_smooth_frames": 7}
```

- `protect_bottom_from: 0.6` é o padrão (protege a faixa de legenda). Quando o rosto fica baixo no quadro (ex.: vídeo
  gravado no carro, nariz/boca em ~0,56–0,65 da altura), 0,6 cortaria o warp do nariz: **meça antes o topo da
  legenda** (amostre 1 quadro/s e ache a linha mais alta do texto) e use ~0,70 (≈40 px acima do topo da legenda,
  que nesses vídeos fica em ~0,72). Num bruto sem legenda pode ficar 0,6 ou ir para `null`.
- Nariz: 0,30 / `nose_alar_boost` 0,35 foi testado e considerado "perceptível" pelo usuário; 0,26 / 0,25 é o nível
  aprovado. Teto absoluto de `nose_tip_reduce`: 0,30.
- Ajustes finos só a pedido dele; nunca mude `beard`, `beard_shape` nem `hairline_shape`.
- O rastreamento do vídeo inteiro fica em cache (`<workdir>/track_cache/`): re-renderizar com outro valor de retoque
  sobre o mesmo arquivo pula os ~10 min de rastreamento.

## Lições aprendidas (não repetir os erros)

1. **NUNCA editar a barba.** O preenchimento/desenho de barba foi testado, ficou ruim e o usuário vetou ("não vale nem
   a tentativa"): `beard: 0.0` e `beard_shape: false`, sempre.
2. **Entradas do cabelo:** só escurecer o couro cabeludo entre fios existentes, com grão casado — sem linha-alvo
   geométrica (`hairline_shape: false`). O retoque não pode parecer edição, e o rosto tem de continuar
   reconhecivelmente o dele. Na dúvida, menos.
3. **Vídeo já editado com legenda:** use o modo de retoque sobre o arquivo final —
   `python scripts/05b_retouch.py polish.json --in polished.mp4 --out retouched.mp4` — com `protect_bottom_from`
   preservando a faixa da legenda; confira o compare (prévias `--preview` antes × depois) antes de entregar.
4. **Cor/pele alaranjada:** no look `natural`, o `vibrance` herdado (0,12) pode alaranjar a pele e anular a correção
   de WB. Com pele já quente (R/B alto), use `saturation` 1.0, `vibrance` −0.1, `white_balance.strength` 1.0, e meça o
   R/B da pele antes e depois.
5. **Trama quadriculada no cabelo (código antigo, corrigido):** sem `hairline_band`, o adensamento tratava o
   reflexo/brilho do topo do cabelo como "couro cabeludo" e pintava ali uma trama de mechas cruzadas, com cara clara de
   edição. Agora o efeito só age numa faixa junto à pele da testa/têmporas (`hairline_band`, padrão 0,07; preset 0,12).
   Sempre amplie o cabelo nas prévias (não só o rosto inteiro) antes de renderizar.
6. **`hairline_shape` tem efeito limitado:** testado (recessão 0,07 e 0,03) sobre o preset — só produz um leve
   escurecimento difuso no alto das têmporas, sem criar fios nem fechar de verdade as entradas. Não compensa; fechar
   entradas exigiria síntese de fios (não implementada). Com `hairline_shape: false`, `hairline_lower_max` e
   `hairline_recession` não têm efeito.

## Formato do `polish.json`

```json
{ "source": "../../../IMG_3506.mov",
  "mode": "raw",
  "framing": "medium",          "fit": "auto",          "static": false,
  "stabilize": "auto",
  "look": "looks/natural.json", "lut": null,
  "exposure": {"auto": true, "target_face_luma": 0.58, "contrast": 0.12},
  "white_balance": {"auto": true, "temperature_shift": 650, "tint": 0.0, "strength": 0.7},
  "denoise": "auto",
  "beauty_intensity": 35,
  "beauty": {"under_eye": true, "eye_brighten": true, "shine_reduction": true},
  "retouch": {"enabled": false, "nose_tip_reduce": 0.15, "hairline": 0.6, "beard": 0.0, "skin_texture": 0.5,
              "hairline_shape": false, "beard_shape": false, "hairline_lower_max": 0.08, "hairline_recession": 0.04,
              "protect_bottom_from": null, "landmark_smooth_frames": 7},
  "audio": {"enabled": true, "highpass_hz": 80, "denoise_db": 12, "deesser": 0.35,
            "mud_db": -2.0, "presence_db": 2.0, "air_db": 1.5, "comp_ratio": 2.5},
  "sharpen": 0.6,
  "fade": {"in_s": 0.0, "out_s": 0.0} }
```

- Só `source` é obrigatório; o resto tem padrão (`scripts/polish_common.py: DEFAULT_POLISH`). Caminhos relativos
  são procurados na pasta do `polish.json` → diretório atual → raiz da skill.
- `saturation`, `vibrance`, `vignette`, `sharpen`, `exposure.contrast/lift_shadows/highlight_rolloff` ausentes
  herdam do look.
- `white_balance.auto` + `temperature_shift: 0` = usa a sugestão da análise; um valor ≠ 0 é decisão sua.
- `lut`: `.cube` do usuário (aplicada após o look; `lut_mix` no look controla a mistura).
- `fade`: evite em `mode raw` se a reel-editor vai cortar o início/fim (o fade cairia num trecho descartado).

## Looks (`looks/*.json`)
Herança por `"inherits": "natural"`. Campos: `contrast`, `lift_shadows`, `highlight_rolloff`, `saturation`,
`vibrance`, `skin_protect`, `shadows_rgb/midtones_rgb/highlights_rgb` (tintas por faixa tonal, −0,1…0,1),
`temperature_bias` (K somados ao WB), `vignette` (0–1), `sharpen`, `lut`, `lut_mix`. Crie novos looks copiando
`cinematic.json`.

## Ajustes posteriores (editam SÓ o `polish.json` e re-rodam `run_all.py`)
| Pedido do usuário | Campo | Etapas refeitas |
|---|---|---|
| "aproxima mais / afasta" | `framing` | reframe e tudo depois |
| "a câmera fica mexendo" | `static: true` | reframe → … |
| "tá tremendo" | `stabilize: true` | stabilize → … |
| "muito laranja / muito azul" | `white_balance.temperature_shift` (±200 K por vez) | grade → beauty → render → QA |
| "muito escuro / claro" | `exposure.target_face_luma` (±0,05) | grade → … |
| "cor lavada / muito saturado" | `saturation`, `vibrance` | grade → … |
| "quero cara de cinema" | `look: looks/cinematic.json` | grade → … |
| "pele plástica" / "quero mais retoque" | `beauty_intensity` (−10 / +10) | beauty → retouch → render → QA |
| "arruma a entrada do cabelo / o nariz" | `retouch.hairline` (±0,1), `retouch.nose_tip_reduce` (±0,05) | retouch → render → QA |
| "arruma a barba" | NÃO fazer — barba é vetada (ver "Lições"); explique ao usuário | — |
| "áudio abafado / com chiado" | `audio.presence_db` +1 / `audio.denoise_db` ±6 | audio → render → QA |
| "muito nítido / borrado" | `sharpen` ±0,2 | render → QA |

## Arquivos em `work/<nome>/polish/`
`analysis.json/.txt`, `analysis_sheet.png` (nunca apagar) · `polish.json` · `track.json` (trajetória do recorte) ·
`reframe.mkv` · `stabilized.mkv` · `graded.mkv` · `beauty.mkv` · `retouch.mkv` (se `retouch.enabled`) · `retouch_preview/` ·
`audio_polished.wav` · **`polished.mp4`** ·
`qa_report.json` · `compare_sheet.png` · `state.json` (cache). Os `.mkv` intermediários são grandes — podem ser
apagados depois da entrega.

## Comandos úteis
- Prévia de 1 quadro para acertar cor sem renderizar tudo: `python scripts/04_grade.py <polish.json> --preview 12.5 --in work/<nome>/polish/reframe.mkv`
- Prévia do retoque (antes | depois): `python scripts/05_beauty.py <polish.json> --preview 12.5 --intensity 50`
- Prévia do retoque avançado (PNG antes|depois + crop do rosto): `python scripts/05b_retouch.py <polish.json> --in <video> --preview 6 11 20 --out-dir work/<nome>/polish/retouch_preview`
- Instantes frontais/nítidos para prévia: `python scripts/05b_retouch.py <polish.json> --in <video> --suggest 4`
- Trecho de 3 s com retoque (estabilidade temporal, áudio copiado): `python scripts/05b_retouch.py <polish.json> --in <video> --clip 10 3 --out teste_3s.mp4`
- Retoque avançado direto num vídeo pronto (ex.: `polished.mp4` → `retouched.mp4`, áudio copiado): `python scripts/05b_retouch.py <polish.json> --in polished.mp4 --out retouched.mp4`
- Refazer a partir de uma etapa: `python scripts/run_all.py <polish.json> --from grade`
- Teste da skill: `python tests/test_pipeline.py`

## Limitações (diga ao usuário quando relevante)
- Tempo: em CPU comum, ~3–5× a duração do vídeo por etapa pesada (reframe, beauty, render) — um bruto de 3 min
  leva ~30–40 min no total. Avise antes de rodar e sugira cortar o bruto antes se ele for muito longo.
- Ampliar um recorte de vídeo 1080p horizontal para 1080×1920 custa nitidez (fator ~1,8×); o resultado fica bom
  no celular, mas não iguala um bruto gravado em vertical/4K. Para o melhor resultado, grave em vertical ou 4K.
- O rastreamento usa o rosto maior/mais contínuo: com duas pessoas, enquadra uma só.
- O retoque é conservador por desenho; não remove olheiras profundas, acne severa nem muda feições.
- Retoque avançado: ~0,25 s/quadro + rastreamento (~0,2 s/quadro) em CPU — 40 s de vídeo ≈ 10 min. O preenchimento de
  cabelo é "escurecer + clonar textura" (não cria fios novos): bom em falhas pequenas e entradas rasas, limitado em
  calvície real. O nariz é um warp local: em perfil forte o efeito diminui sozinho (campo elíptico centrado no bulbo).

## Nariz × yaw e preset de voz (2026-10-23)
- 05b atenua só o warp do NARIZ quando a cabeça vira (yaw estimado pelos landmarks): força total até `nose_yaw_full`
  (18°), zero a partir de `nose_yaw_zero` (32°), antecipando `nose_lookahead_s` (0,35 s); `nose_off_ranges` zera à força.
  Grava `nose_gain.csv` no workdir. Para corrigir só um trecho: `05b_retouch.py polish.json --clip INI DUR --out x.mkv` e
  substituir os quadros no retouch.mkv. Pele/cabelo inalterados; barba nunca.
- Voz: `"audio": {"preset": "voz_gabriel"}` (presets/voz_gabriel.json): corpo +2 dB/150 Hz, caixa −1,5 dB/450 Hz,
  presença +1,2 dB/3,2 kHz, ar +1 dB/10 kHz, de-esser, compressão 2:1. Sem pitch/formant shift.
