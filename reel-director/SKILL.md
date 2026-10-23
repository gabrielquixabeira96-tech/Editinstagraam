---
name: "reel-director"
description: "Editor profissional de Reels 9:16 com foco em alcance: o Claude escreve um plan.json (EDL) escolhendo para cada trecho A-roll, B-roll real, figura de artigo, cena IA rotulada ou gráfico animado Remotion, com transição e SFX justificados; scripts executam (ffmpeg, Remotion, MediaPipe) e um QA reprova o que viola as regras de alcance. Integra reel-polish (retoque preset gabriel), reel-editor (transcrição/legenda/mix) e narracao-voz-gabriel (Fastcloner)."
---

# reel-director

**Papel:** o Claude DIRIGE (escreve `plan.json`), os scripts EXECUTAM. Pasta: `/home/claude/reel-director-skill/`.
Regras de alcance com fonte: `research/alcance-2026.md`. Parâmetros: `styles/director.json` (fonte única).

> **Estado (2026-10-22):** pipeline completo e testado ponta a ponta. `python3 scripts/run_all.py tests/plan_exemplo.json --approve-plan`
> roda do zero com o vídeo sintético e o QA passa (gancho 2,0 s, maior plano estático 2,1 s, −14,2 LUFS, TP −1,2).
> Saída em `tests/out/demo_sintetico.mp4` (+ `capa_demo.jpg`, `contato_demo.png`).

## Regras invioláveis
1. **Voz sempre do Gabriel:** áudio original ou Fastcloner (`narracao-voz-gabriel/narra.py`). `FASTCLONER_API_KEY` só em `.env` criado com `umask 077`, carregado por `set -a && . ./.env && set +a`. Nunca no código, .md, relatório ou memória. Sem chave: pular a etapa e avisar.
2. **Retoque = preset `gabriel` da reel-polish** (nose_tip_reduce 0.26, nose_alar_boost 0.25, hairline 1.0, hairline_band 0.12, skin_texture 0.75, beard 0.0, beard_shape false, hairline_shape false, landmark_smooth_frames 7, protect_bottom_from 0.6, ou ~0.70 com rosto baixo/carro). Barba: nunca. Nunca despersonalizar. Código: `GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/gabrielquixabeira96-tech/editinstagraam /home/claude/gabrielquixabeira96-tech/editinstagraam` e depois `python3 .../reel-polish/scripts/05b_retouch.py polish.json --in X --out Y` (polish.json: `"retouch": {"preset": "gabriel"}`).
3. **Ilustração dele nunca é substituída por IA**: só recortar, mascarar ou anotar. **Anatomia nunca por IA.** Cena IA sempre com rótulo "Recriação ilustrativa (IA)" (composição `Label`).
4. **Nível visual alto:** figuras reais de artigos com crédito na tela, B-roll real, animações com easing. Proibido: emoji, neon, gradiente chamativo, wipe, giro, page curl, ilustração infantil.
5. **Identidade:** Poppins Bold, branco e acento `#39E58C`, caixas `rgba(8,12,18,.82)` com raio 28 e borda verde de 6 px.
6. **CFM 2.336/2023:** sem promessa de resultado, nunca "especialista", lower third "Dr. Gabriel Quixabeira · CRM-MT 14.954", fecho "Conteúdo educativo. Não substitui avaliação médica.".
7. **Custo em degraus:** (1) local grátis; (2) SFX/música Runway (1 a 4 créditos) com aviso; (3) `generate_video` (~11 créditos/s a 480p), `remove_video_background` (1 crédito/s) e `edit_video`/`expand_video` (28 créditos/s) **só com confirmação explícita** do Gabriel, depois de mostrar o saldo (`show_plans_and_credits`). Uma chamada por cena, sem retry. ElevenLabs não gera vídeo nesta conta.
8. **Não duplicar as irmãs:** transcrição em `reel-editor/scripts/01_transcribe.py` (Whisper só para os tempos; o texto vem do roteiro), cortes/timemap em `02_apply_edl.py`, legenda em `03_subtitles.py`, mix em `05_audio.py`, reel em tela de aula em `reel-aula-artefato`. Nunca editar `/mnt/skills/user/*`.

## Fluxo (um comando, cache por etapa)
```bash
python3 scripts/run_all.py plan.json                 # ingest→transcribe→direct, imprime o plano e PARA
#   mostre work/<nome>/plano.txt ao Gabriel; com o ok:
python3 scripts/run_all.py plan.json --approve-plan  # roda o resto até o QA + capa + post
python3 scripts/run_all.py plan.json --approve-plan --from compose   # recomeça de uma etapa
#   flags: --no-polish  --no-ai  --from <etapa>  --force
```
Etapas e artefatos em `work/<nome>/` (o runner chama exatamente isto; use avulso para refazer uma parte):
1. **ingest** `01_ingest.py plan.json` → `ingest.mp4` (1080×1920, 30 fps CFR, rotação aplicada, HDR HLG/PQ→BT.709, horizontal sobre fundo desfocado) + `ingest.json` (`chroma.detected` quando ≥ 50 % da borda é verde). Troca a voz pela narração Fastcloner quando `narration.mode=="fastcloner"`.
2. **transcribe** `reel-editor/01_transcribe.py` (pulado se existir; Whisper só para os tempos — o texto vem do roteiro).
3. **direct** `02_direct.py plan.json` → valida o plano, escreve `edl.json` (formato reel-editor) + `plano.txt` + `direction.json`. Para aqui se o plano não estiver aprovado.
4. **polish** (opcional, `polish.enabled`): reel-polish com `retouch.preset=gabriel` → `polish/polished.mp4`.
5. **cut** `reel-editor/02_apply_edl.py` → `cut.mkv` + `timemap.json`.
6. **graphics** `04_graphics.py plan.json` → `jobs.json` → `render.mjs` → sequências PNG RGBA por gráfico + `graphics_manifest.json` (com a caixa alfa, que o QA usa). Bundle ~4 s, ~0,1 s/quadro.
7. **compose** `05_compose.py plan.json` → `director.mkv`: passo 1 base (shots + transições `vfx.transition` + punch-in), passo 2 overlays (chroma `vfx.key_green/refine/light_wrap/color_match`, gráficos PNG, texto atrás da pessoa por segmentação).
8. **subtitles** `reel-editor/03_subtitles.py --video director.mkv` → `subs.mkv` (legenda palavra a palavra por cima de tudo).
9. **audio** `06_audio.py plan.json` → `audio_edl.json` (SFX presos às transições whip/zoom_through = whoosh; punch-in = pop; espaçamento mínimo) → mix pela reel-editor (ducking + `loudnorm` −14 LUFS/TP −1) → `mix.wav`.
10. **render** `reel-editor/06_render.py` → `final.mp4` (H.264 High, 30 fps, BT.709).
11. **qa** `07_qa.py plan.json` → `qa_report.json` (reprova: gancho renderizado > 2 s; plano estático > 4 s; gráfico fora de x 65–1015, no topo, ou sobre a faixa da legenda com legenda ativa; loudness fora de −14 ± 1 / TP > −1; cena IA sem rótulo; duração > 60 s sem `justify_long`). Nada é entregue reprovado.
12. **post** `08_post.py plan.json` → `capa_4x5.jpg` (1080×1350) + `post.txt` (sério, SEM emoji, CTA de **envio**, fecho educativo, assinatura CRM).

## plan.json (gramática de edição)
```json
{"source":"bruto.mp4","style":"director","keep":[{"start":6.2,"end":16.9}],
 "shots":[{"t":0,"dur":4.6,"kind":"aroll"},
          {"t":4.6,"dur":3,"kind":"broll","file":"assets/x.mp4","in":"zoom_through","why":"mostra o instrumental citado"},
          {"t":7.6,"dur":3,"kind":"aroll","in":"dissolve"}],
 "graphics":[{"t":0,"dur":1.5,"comp":"HookTitle","props":{"lines":[["PROTETOR","SOLAR"],["*2","REGRAS*"]]},"why":"R1/R5"},
             {"t":8,"dur":2,"comp":"Counter","props":{"to":30,"suffix":"min","label":"antes de sair"},"why":"reforça número falado"}],
 "zoom":[{"t":2,"scale":1.12}],"sfx":[{"t":4.6,"name":"whoosh"}],
 "chroma":{"enabled":false},"narration":{"mode":"original"},
 "ai_policy":{"allow":false,"confirmed_by_user":false},
 "post":{"caption":"...","cta":"Manda para quem esquece o protetor."}}
```
Todo item tem `why`. Tempos na timeline ORIGINAL (convertidos por `map_time` do timemap).

### Quando usar cada recurso
| Recurso | Quando usar | Limite |
|---|---|---|
| A-roll (rosto) | Padrão: confiança e voz | plano ≤ 4 s sem evento (punch-in, gráfico ou corte) |
| B-roll real (Adobe Stock, filmagem dele) | Quando a fala cita objeto/lugar/ação visível | 1,2–5 s; não usar como enfeite (R8) |
| Figura de artigo (`ArticleFigure`) | Dado, achado ou imagem de estudo; crédito "Autor, Revista, ano, CC BY" visível | só CC BY/CC0 (checar licença) |
| Cena IA (Runway) | Só o que não dá para filmar e não é anatomia | rótulo obrigatório; confirmação + saldo |
| `HookTitle` (kinetic typography) | 0–2 s, promessa em ≤ 6 palavras, 1 palavra em acento | obrigatório |
| `Counter` / `BarChart` / `LineChart` | Número ou comparação falada | 1,5–3 s, com fonte |
| `Callout` | Apontar uma estrutura no quadro (instrumento, região) | com rótulo curto |
| `ProgressBar` | Reel em lista/passos > 25 s | y 280, fina |
| `LowerThird` | Uma vez, após o gancho (CRM) | fora da coluna de ações |
| `BigText` + segmentação (texto atrás da pessoa) | Palavra-tema forte, 1 vez | MediaPipe local |
| `Label` | Rótulo IA, disclaimer, CTA | sempre no fecho |
| Chroma key | Só se `chroma.detected`; senão segmentação MediaPipe local; Runway `remove_video_background` só com confirmação | erode 2 px, feather 3 px, light wrap 0,35, color match 0,5, fundo desfocado com parallax |
| Ken Burns / parallax em foto | Toda imagem parada | 1,00→1,08 com easing |

### Transições (biblioteca `vfx.transition`)
| Tipo | Duração | SFX | Quando |
|---|---|---|---|
| corte seco | 0 | — | PADRÃO; jump cut de fala |
| J-cut / L-cut | áudio ou imagem 0,25 s adiantados | — | suavizar jump cut entre frases |
| match cut | 2 quadros de fusão | — | forma ou posição igual nos dois planos |
| dissolve | 0,35 s | — | passagem de tempo/tom, entrada de figura, fecho (máx. 2) |
| luma fade | 0,5 s | — | entrar/sair de cena clara (centro cirúrgico, IA) |
| whip | 0,24 s, blur horizontal | whoosh | virada de bloco ("mas…") (máx. 1) |
| zoom-through / push-in | 0,4 s, blur radial | whoosh | do rosto para o detalhe (máx. 1) |
| punch-in | 1,10–1,15 em 0,25 s | pop | palavra-chave, ≤ 1 a cada 8 s |

## Receitas testadas (ffmpeg 6.1, 2 vCPU)
- Normalizar antes de qualquer xfade: `fps=30,setsar=1,settb=1/30,format=yuv420p` em **todas** as entradas.
- Push-in/zoom-through: `xfade=transition=zoomin:duration=0.4:offset=T`. Dissolve: `xfade=transition=fade:duration=0.35`.
- Punch-in: `scale=w='iw*(1+0.12*min(max((t-T)/0.25,0),1))':h=-2:eval=frame,crop=1080:1920`.
- Chroma: `chromakey=0x00FF00:0.15:0.06,despill=type=green`, depois `overlay` (para acabamento fino use `vfx.key_green/refine_alpha/light_wrap/color_match`).
- WEBM com alfa do Runway: `-c:v libvpx-vp9` **antes** do `-i`.
- Overlay Remotion: sequência PNG (render.mjs) ou ProRes 4444 → `overlay=0:0:eof_action=pass` com `setpts=PTS+T/TB`.
- Mix: `[voz]asplit[vo][sc];[mus]volume=0.35[m];[m][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];[sfx]adelay=ms|ms[wh];[vo][duck][wh]amix=inputs=3:normalize=0:duration=first,loudnorm=I=-14:TP=-1.5:LRA=11`.
- Figura de artigo: Europe PMC `rest/search?query=...AND OPEN_ACCESS:y AND LICENSE:"cc by"` → `rest/<PMCID>/fullTextXML` (ler `<graphic xlink:href>`) → baixar em `https://pmc.ncbi.nlm.nih.gov/articles/instance/<n>/bin/<href>.jpg` com `User-Agent: Mozilla/5.0` (testado com PMC12187030).
- Legenda palavra a palavra: `reel-editor/03_subtitles.py --ass-only` (ou `words2ass.py` para tempos prontos), queimar por último.

## Safe zones (1080×1920)
Destaque em x 65–1015, y 470–1250. Legenda acima de y 1250 (≈1190). Topo 0–270 livre. Coluna de ações x > 850 e y > 1150 livre.

## Teste
```bash
python3 scripts/run_all.py tests/plan_exemplo.json --approve-plan
```
Roda do zero com `/mnt/skills/user/reel-editor/tests/sample_input/sample_01_vertical.mp4` e `tests/plan_exemplo.json`.
Saída `tests/out/demo_sintetico.mp4` (10,8 s; 1080×1920; 30 fps; −14,2 LUFS; TP −1,2): gancho animado (kinetic typography),
punch-in, **3 transições** (zoom_through, dissolve, whip), B-roll com Ken Burns implícito, contador Remotion que conta até
30, chroma key com despill/light-wrap, legenda palavra a palavra, lower third, disclaimer, música com ducking e whoosh preso
às transições. **QA aprovado.** Pranchas: `tests/out/contato_demo.png` e `tests/out/capa_demo.jpg`.
`tests/test_vfx.py` cobre as transições e o chroma isoladamente. (O sample é sintético: o gancho aparece sobre o
timecode queimado do próprio arquivo de teste — isso não ocorre com um bruto real.)
