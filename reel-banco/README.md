# reel-banco — banco de material pesquisável para Reels

Um depósito único (pasta do Google Drive) onde o Gabriel joga **vídeos longos** (aulas, lives, gravações de
celular), **PDFs** (artigos, capítulos), **documentos** e **artefatos HTML** (aulas-atlas). A skill transforma
cada arquivo num **item** com transcrição palavra a palavra, cenas medidas, miniaturas e um conjunto de
**segmentos** catalogados (tema, tese, citação exata, potencial de gancho, públicos, usos). A cada pedido de
Reel novo, o Claude vasculha o banco inteiro com busca BM25 antes de escrever roteiro ou editar, e entrega os
trechos escolhidos à `reel-director` como clipes/B-roll.

```
Drive 01_entrada ──(conector: listar)──▶ manifesto ──01_scan──▶ novos
   │                                                         │
   └──drive_fetch (link público)──▶ work/banco/<id>/source.* ◀┘
                                         │
      vídeo: 03_transcribe → 04_scenes → 05_keyframes → 06_segment_input ─┐
      doc:   07_doc_ingest (PDF: texto+figuras · HTML: seções+capturas) ──┤
                                                                          ▼
                       Claude LÊ segment_input.md/doc_input.md e ESCREVE segments.json
                                                                          │
                     run_ingest --finish = 06b_validate → index/items/<id>/ → 08_index
                                                                          │
                 Reel novo: index.md + 09_search (3–6 consultas) → Ficha de material
                                                                          │
                       10_clip --broll ──▶ reel-director/assets/broll/index.json
```

## Instalação

```bash
git clone https://github.com/gabrielquixabeira96-tech/editinstagraam ~/reel-polish-src
cd ~/reel-polish-src/reel-banco
pip install -r requirements.txt          # em Linux gerenciado pelo sistema: --break-system-packages
python scripts/00_check_env.py
python tests/test_pipeline.py            # ponta a ponta com material sintético (sem rede)
python tests/test_memory.py              # 1 h de áudio sintético: pico de RSS da transcrição ≤ 2,5 GB (~1 h em 2 vCPU)
```

- **ffmpeg/ffprobe** no PATH (filtros `scdet`, `astats`, `silencedetect`, `tile`). Variáveis `REEL_FFMPEG`/`REEL_FFPROBE`.
- **faster-whisper**: modelo `small` (~480 MB) baixa na 1ª transcrição para `~/.cache/huggingface`. A
  transcrição é feita em janelas de 10 min lidas do ffmpeg (sobreposição 1,5 s, palavra atribuída à janela que
  contém o seu meio): memória limitada a uma janela — `tests/test_memory.py` exige pico < 2,5 GB com 1 h de áudio.
  VAD ligado, prompt só de vocabulário, palavras de segmentos com `no_speech_prob ≥ 0,6` descartadas.
  Outro modelo: `REEL_WHISPER_MODEL=medium`. Contorno embutido: o áudio é decodificado com ffmpeg e passado
  como array (faster-whisper 1.2.1 + PyAV 19 quebram com caminho de arquivo).
- **Playwright + Chromium** só para HTML (`playwright install chromium` se ainda não houver). Sem Chromium, o
  HTML é ingerido só como texto (aviso).
- **YuNet** (detecção de rosto) é reaproveitado de `../reel-polish/models/face_detection_yunet_2023mar.onnx`
  (ou `REEL_YUNET`).
- Pastas: `REEL_BANCO_WORK` (padrão `reel-banco/work/banco`) e `REEL_BANCO_INDEX` (padrão `reel-banco/index`).

## Google Drive

`config.json` guarda os ids das pastas:

| chave | pasta | uso |
|---|---|---|
| `root` | raiz do banco | — |
| `entrada` | `01_entrada` | onde o Gabriel joga o material |
| `processado` | `02_processado` | destino depois de indexado |
| `clipes` | `03_clipes` | clipes exportados (opcional) |
| `indice` | `04_indice` | contact sheets (opcional) |

O conector Drive do Claude lista, lê documentos pequenos e move arquivos, mas **não transfere binários grandes
nem altera compartilhamento**. Os vídeos são baixados por `scripts/drive_fetch.py` pelo link público, então a
pasta `01_entrada` precisa estar como **"Qualquer pessoa com o link – Leitor"** (ação única do dono).
`drive_fetch.py <file_id> <destino> [--expect-size N]` trata o aviso de arquivo grande, grava em `.part`, confere
o tamanho e sai com 3 (não compartilhado), 4 (HTML inesperado/cota, host não permitido, 5xx, rede) ou 5
(tamanho divergente). Não segue redirecionamentos automaticamente: segue `Location` à mão (máx. 5) e só aceita
https em `google.com`, `*.google.com` e `*.googleusercontent.com` (inclusive o `action` do formulário de aviso). É uma
implementação provisória e pode ser substituída mantendo essa interface.

## Pipeline e scripts

Todos em Python 3, com `--help`, idempotentes; cache por etapa em `work/banco/<item_id>/state.json` e log em
`work/banco/<item_id>/log.txt`.

| script | entrada → saída |
|---|---|
| `00_check_env.py` | verifica Python, libs, ffmpeg/filtros, Chromium, YuNet, modelo whisper no cache, config |
| `01_scan.py --manifest m.json` | listagem da 01_entrada (id, name, mimeType, size, modifiedTime) → novos / já ingeridos (por `drive_id` ou item_id+tamanho) / ignorados |
| `02_fetch.py --file-id ID --name N [--size B]` · `--local P` · `--item ID` | `work/banco/<id>/source.<ext>` + `item.json` (origem, tipo, duração/resolução via ffprobe, tamanho, sha256, licença) |
| `03_transcribe.py <id> [--import-transcript T] [--replace [--no-carry-edits]]` | `transcript.json` (formato reel-editor + `raw` por palavra) + `transcript.txt` (marca a cada ~20 s); nunca retranscreve, exceto `--replace` (transcrição defeituosa: backup `.vN`, retranscrição e reaplicação tolerante das correções de `word`) |
| `04_scenes.py <id>` | `scenes.json`: cortes (`scdet`), blocos de ≤ 30 s em câmera fixa, e por cena rosto (YuNet), nitidez, luma, estourado/escuro, nível de áudio e silêncio, notas sugeridas |
| `05_keyframes.py <id>` | 1 quadro por cena e ≥ 1 a cada 30 s → `keyframes/`, `index/items/<id>/thumbs/*.jpg` (320 px), `contact_sheet.jpg` (grade numerada com tempos; folhas extras para vídeos longos) |
| `06_segment_input.py <id>` | `segment_input.md`: transcrição em blocos de 60 s + tabela de cenas + miniaturas, instruções e esquema |
| `06b_validate_segments.py <id>` | valida esquema/valores, ajusta t0/t1 às palavras, confere citação literal (grava `citacao_t0/t1`), renumera ids |
| `07_doc_ingest.py --local P --licenca L [--fonte F]` | PDF: texto por página, figuras com página/bbox/legenda próxima, render de páginas; HTML: seções por h1–h3 + capturas 540×960 (@2x); `doc_text.json`, `figuras.json`, `doc_input.md`, thumbs e contact sheet |
| `08_index.py` | `index/index.json`, `index/index.md` (1 linha por segmento), `index/bm25_tokens.json` |
| `09_search.py "q" [--tags] [--tipo] [--publico] [--uso] [--min-gancho] [--top] [--json]` | BM25 (sem acentos, stopwords pt, plural simples) + bônus de tag/público/uso/gancho |
| `10_clip.py "<seg>" [--pad 0.3] [--precise] [--broll --broll-index P] [--for-editor DIR]` | vídeo → `work/banco/clips/<seg>.mp4` (≤ 1080p, H.264 CRF 18 / AAC 192k) + `<seg>.transcript.json`; `--precise` = busca híbrida exata; `--for-editor DIR` grava `DIR/<clipe>/transcript.json` para a reel-editor/reel-director; doc → figura `.png/.jpg` (figuras.json lido do índice se work/ estiver vazio); `--broll` registra em `assets/broll/index.json` da reel-director |
| `run_ingest.py` | `--file-id/--local`: 02→03→04→05→06 (ou 07) e para; `--finish <id>`: 06b → cópia para index/ → 08 |
| `banco_common.py` | caminhos, ffmpeg/ffprobe, logs, JSON atômico, slug/ids, tokenização, cache de etapas |

## Formatos

### item.json
```json
{"id": "20261020_aula-vias-aereas", "nome": "aula vias aereas", "tipo": "video",
 "origem": {"drive_id": "1AbC...", "nome_original": "aula vias aereas.mp4", "modifiedTime": "2026-10-20T14:03:11Z"},
 "arquivo": "source.mp4", "tamanho": 734003200, "sha256": "...", "duracao": 3721.4, "largura": 1080, "altura": 1920,
 "fps": 30.0, "tem_audio": true, "licenca": "propria", "status": "indexado", "n_cenas": 140, "n_keyframes": 152,
 "contact_sheets": ["contact_sheet.jpg", "contact_sheet_02.jpg"], "n_segmentos": 38}
```
`status`: `baixado` → `aguardando-segmentos` → `segmentado` → `indexado`. Documento: `subtipo` (pdf|html),
`paginas`/`secoes`, `n_figuras`, `fonte`, `licenca` obrigatória (`propria|terceiros|desconhecida`).

### transcript.json (idêntico à reel-editor `01_transcribe.py`)
```json
{"source": "...", "language": "pt", "model": "small", "duration": 3721.4, "generated_by": "reel-banco/03_transcribe.py",
 "refined_boundaries": true,
 "words": [{"word": "cricotireoidostomia", "start": 12.48, "end": 13.52, "prob": 0.91, "raw": "cricotireoidostomia",
            "raw_start": 12.4, "raw_end": 13.6}],
 "segments": [{"start": 10.2, "end": 15.9, "text": "...", "avg_logprob": -0.21, "no_speech_prob": 0.01}]}
```
`raw` (desde 2026-10-24) = texto que o whisper escreveu; correções vão só em `word`, e `raw` ≠ `word` marca a
correção humana que o `--replace` reaplica. É campo extra: quem lê só `word`/`start`/`end` não muda. Depois de um `--replace`, o
transcript ganha `substitui` (backup, palavras antes/depois, `correcoes_detectadas`, `edicoes_reaplicadas`,
`ja_corretas`, `nao_reaplicadas`).
Mesma aparagem de limites por energia (`refine_boundaries`) e mesmos parâmetros (`word_timestamps=True`,
`beam_size 5`, `condition_on_previous_text=False`, prompt inicial em pt-BR).

### segments.json
Lista de `{id, t0, t1, tema, tags[], tese, citacao, gancho_potencial 0-5, publicos[], qualidade_visual 0-5,
qualidade_audio 0-5, usos[], notas}` + campos gravados pelo validador (`dur`, `citacao_t0`, `citacao_t1`,
`citacao_palavras`, `t0_original`/`t1_original`, `item_id`). Documentos: `t0`/`t1` nulos e `ref`
(`pagina`, `figura`, `secao`, `secao_id`).
`publicos` ∈ plantonista · residente · candidato-residencia · concursos-vestibular · geral.
`usos` ∈ fala-principal · broll · corte-rapido · citacao-card.

### Entrada no B-roll da reel-director (`assets/broll/index.json`)
```json
{"file": "assets/broll/20261020_aula-vias-aereas_s03.mp4", "source": "Google Drive id 1AbC...", "work": "aula vias aereas",
 "what": "por que a crico é por punção na criança", "license_note": "material próprio do Gabriel", "tag": "via-aerea",
 "tags": ["via-aerea", "pediatria"], "segment_id": "20261020_aula-vias-aereas#s03", "kind": "video", "dur": 6.7,
 "t0": 125.1, "t1": 131.8, "licenca": "propria", "added_by": "reel-banco/10_clip.py"}
```
Os cinco primeiros campos são os do banco de cenas da reel-director; os demais são extras (ignorados por ela).

## O que vai para o git

Lista positiva no `.gitignore`: de `index/` só entram `*.json`, `*.md`, `items/*/thumbs/*.jpg` e
`items/*/contact_sheet*.jpg` (transcript.json gravado compacto). O BM25 é versionado como **tokens em JSON**
(`bm25_tokens.json`) e reconstruído na busca — nada de pickle no repositório (carregar pickle executa código).
Não entram: `work/` (mídia original, keyframes em resolução cheia, figuras, clipes). Vídeos com `origem.drive_id`
são rebaixados sozinhos por `02_fetch.py --item <id>` (o `10_clip.py` faz isso); itens ingeridos com `--local`
sem `--drive-id` e documentos precisam ser reabastecidos (ver SKILL.md, "Reabastecer work/").

## Limites e cuidados

- Transcrição em CPU: ~0,3–1× o tempo real com `small` (1 h de live ≈ 20–60 min). Rode em segundo plano.
- Cortes de cena em câmera fixa quase não existem: o vídeo é dividido em blocos de 30 s para as medidas
  continuarem locais. `--threshold` menor detecta mais cortes.
- As notas `sug_visual`/`sug_audio` são heurísticas (nitidez, exposição, rosto, nível); quem decide é o Claude
  olhando as miniaturas.
- PDF escaneado (sem camada de texto) não tem texto extraível — as páginas viram imagem e a citação não pode
  ser conferida (use `--lenient-citacao` no validador e registre em `notas`).
- Figuras de PDF vêm das imagens embutidas; gráficos vetoriais não são extraídos como figura (use o render da página `pN.jpg`).
- BM25 é lexical: a qualidade da busca depende de `tema`, `tags` (inclua sinônimos) e de rodar várias consultas.
- Material de terceiros: só com `licenca` e `fonte` registradas e crédito na tela.
- **Cota de download do Drive por arquivo:** baixar o mesmo vídeo grande muitas vezes no dia faz o link público
  devolver "Quota exceeded" (`drive_fetch` sai com código 4) por horas. Em 2026-10-23 isso aconteceu com o
  IMG_3502 (6,6 GB) e não liberou em 2 h de tentativas; liberou na manhã seguinte. Não contorne copiando o
  arquivo no Drive (ocupa o armazenamento do Gabriel); espere a cota e teste com
  `python3 scripts/drive_fetch.py <drive_id> - --probe-bytes 65536` (código 0 = liberado).
- **`--replace` e correções manuais:** o whisper não sai bit-idêntico entre execuções (no IMG_3502, tempos
  deslocados ~20 ms, pontuação/caixa e algumas palavras diferentes). Até 2026-10-24 as correções de `word` eram
  casadas por start/end idênticos: só 9 das 28 voltaram e 30 "reaplicadas" eram diferenças entre execuções. Agora
  correção = `word` ≠ texto bruto (`raw` da palavra; sem ele, o texto de `segments`), casada por tempo (≤ 150 ms,
  início ou fim) + similaridade do bruto (≥ 0,6), e diferença entre execuções nunca é tocada; no mesmo par real,
  25 das 28 voltam sozinhas (2 delas já vinham certas) e 0 espúrias. O que não casa fica em
  `transcript.json → substitui.nao_reaplicadas` (impresso no fim do comando, com o trecho novo e o `motivo`:
  `estrutural`, `sem_casamento`, `sem_bruto`) e precisa ser corrigido à mão. Teste de regressão com o par real:
  `tests/fixtures/img3502_replace.json.gz` (recriável com `python3 tests/test_pipeline.py --rebuild-3502`).
