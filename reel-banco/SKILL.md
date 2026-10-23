---
name: reel-banco
description: Banco de material pesquisável para Reels do Gabriel — ingere vídeos longos (aulas, lives, gravações), PDFs, documentos e artefatos HTML deixados na pasta 01_entrada do Google Drive, transcreve (faster-whisper, formato reel-editor), detecta cenas, mede qualidade, gera miniaturas/contact sheet e um catálogo de segmentos (tema, tese, citação exata, gancho, públicos, usos) com busca BM25; recorta trechos como clipes e os entrega como B-roll à reel-director. Use SEMPRE que o Gabriel pedir um Reel novo (antes de qualquer roteiro ou edição, vasculhe o banco), quando ele disser "joguei material no banco/na pasta", "processa o banco", "ingere esse vídeo/PDF/atlas", "o que eu tenho sobre X", "acha um trecho em que eu falo de Y", ou pedir B-roll/citação/figura de material próprio.
---

# reel-banco

## Passo 0 — instalar/atualizar a partir do GitHub (SEMPRE, antes de qualquer outro comando)

A fonte da verdade é o repositório `gabrielquixabeira96-tech/editinstagraam` (pasta `reel-banco/`), o MESMO da
reel-polish, clonado em `~/reel-polish-src`.

```bash
if [ ! -d ~/reel-polish-src/.git ]; then
  git clone https://github.com/gabrielquixabeira96-tech/editinstagraam ~/reel-polish-src
else
  git -C ~/reel-polish-src pull --ff-only
fi
pip install --break-system-packages -r ~/reel-polish-src/reel-banco/requirements.txt
cd ~/reel-polish-src/reel-banco && python3 scripts/00_check_env.py
```

- Pasta da skill: `~/reel-polish-src/reel-banco/` (rode tudo de dentro dela). O clone precisa ser **completo**
  (sem `--depth 1`) quando for fazer `git push` do índice; se o clone raso já existe, `git fetch --unshallow`.
- Se o clone ou o push falhar por acesso, use primeiro `mcp__claude-code-remote__add_repo` (owner `gabrielquixabeira96-tech`, repo `editinstagraam`, access `push`); só se isso falhar, peça ao Gabriel para anexar o repositório à sessão.
- `config.json` → `drive`: ids das pastas `root`, `entrada` (01_entrada), `processado` (02_processado),
  `clipes` (03_clipes), `indice` (04_indice).
- Contorno já embutido: faster-whisper 1.2.1 + PyAV 19 quebram com caminho de arquivo; `03_transcribe.py`
  decodifica com ffmpeg e passa array numpy. Não "conserte" isso.

## Passo 0.5 — checagem prévia (obrigatória)

Antes de baixar, transcrever, ingerir ou editar qualquer coisa, rode a checagem única das skills de Reels
(1–2 min; está na raiz do mesmo repositório):

```bash
python3 /home/claude/Editinstagraam/preflight.py --fix     # ou <clone em uso>/preflight.py, ex.: ~/reel-polish-src/preflight.py
python3 /home/claude/Editinstagraam/preflight.py --estimar <duração_do_bruto_s> <duração_do_reel_s>   # prazo do caso
```

- Ela imprime uma tabela **OK / AVISO / FALHA** com a ação de cada falha. Confere: links do Drive (01_entrada e
  02_processado; lê só 64 KB de um arquivo de cada pasta com `drive_fetch.py <id> - --probe-bytes 65536`; código 3 =
  pasta sem link), scripts da reel-director, `node_modules` do Remotion, Chromium local, `FASTCLONER_API_KEY`,
  `~/reel-polish-src`, reel-editor, modelos YuNet/MediaPipe/RNNoise, ffmpeg (libass, vid.stab, arnndn), módulos
  Python, faster-whisper `small` em cache, disco livre (cota fixa), RAM e CPUs.
- `--fix` corrige só o que é seguro e local, sem sobrescrever nada: cria os symlinks esperados (`~/reel-polish-src`
  -> clone, `/home/claude/reel-director(-skill)`), copia do clone os arquivos que faltam, roda `npm ci` em
  `templates/remotion`, aponta o Chromium de `/opt/pw-browsers`, instala módulos com pip, baixa modelos e o whisper.
  Clona o repositório se `--root` estiver vazio. **Nunca rode `playwright install`.**
- **FALHA restante: resolva antes de começar.** O que depende do Gabriel sai no bloco "AVISAR O GABRIEL AGORA"
  (pasta do Drive sem link, chave Fastcloner, skill reel-editor ausente): avise já, na primeira resposta, com a ação
  exata, e siga com o resto em paralelo — nada de descobrir isso no meio da ingestão.
- **Prazo:** informe ao Gabriel só com base na tabela de estimativa que o preflight imprime (medida em 2 vCPU,
  escalada pelas CPUs da sessão) mais a margem indicada — nunca um número de cabeça. Referência: ingestão de vídeo de
  10 min ≈ 6–8 min; transcrição ≈ 0,45 × duração; download 28–100 MB/s.
- Ingestão longa: siga também a seção "Execução longa sem supervisão" da reel-director (checagens a cada 8–10 min).

**Princípio:** os SCRIPTS extraem, medem e indexam; **você (Claude) LÊ e DECIDE** — escreve `segments.json`
a partir de `segment_input.md`/`doc_input.md`. Nunca improvise ffmpeg/whisper avulso para um item: se faltar
recurso, vira opção de script.

## Onde fica cada coisa

| O quê | Onde | Git? |
|---|---|---|
| Mídia original, keyframes, figuras, clipes | `work/banco/<item_id>/`, `work/banco/clips/` e Drive | **nunca** |
| item.json, transcript.json, segments.json, scenes.json, thumbs 320 px, contact_sheet.jpg | `index/items/<item_id>/` | sim |
| Catálogo e busca | `index/index.json`, `index/index.md`, `index/bm25_tokens.json` | sim |

`item_id = <AAAAMMDD>_<slug-do-nome>` (data = modifiedTime do Drive) · `segment_id = <item_id>#s03`.

## Limites do conector Drive (importante)

- O conector **não transfere binários grandes** e **não altera compartilhamento**. Vídeos são baixados por
  `scripts/drive_fetch.py` via link público — por isso a pasta **01_entrada precisa estar como "Qualquer pessoa
  com o link – Leitor"** (ação única do Gabriel; se `drive_fetch` sair com código 3, peça isso a ele).
  A pasta **02_processado também precisa estar assim** (já está desde 2026-10-23): depois do passo 7 os vídeos
  moram nela e o rebaixamento (`02_fetch --item`, `10_clip`) depende do link.
- Documentos pequenos (PDF, HTML, Google Docs exportado): baixe pelo conector (`download_file_content` /
  `read_file_content`) para um caminho local e ingira com `--local`.
- Artefatos claude.ai: leia com a ferramenta Artifact (`action: "read"`), salve o HTML localmente e ingira com `--local`.
- Listagem e movimentação são pelo conector (`search_files` com `parentId`, `update_file`), nunca por script.

## Modo 1 — INGERIR (quando ele joga material na pasta ou pede "processa o banco")

1. **Listar `01_entrada`** pelo conector: `search_files` com `parentId` = `config.drive.entrada`
   (pagine até o fim). Monte `work/manifest.json` com `[{id, name, mimeType, size, modifiedTime}]`.
2. `python3 scripts/01_scan.py --manifest work/manifest.json` → lista NOVOS (com comando sugerido),
   já ingeridos e ignorados.
3. Para cada novo:
   - **vídeo:** `python3 scripts/run_ingest.py --file-id <id> --name "<name>" --size <size> --modified "<modifiedTime>"`
     (baixa → transcreve o arquivo INTEIRO → cenas → keyframes → `segment_input.md`). Vídeo longo em CPU:
     transcrição ~0,3–1× o tempo real com `small` — avise o Gabriel do tempo e rode em segundo plano. A
     transcrição é feita em janelas de 10 min (memória limitada por janela, ~1,5–2,5 GB independentemente da duração do vídeo).
     Vídeo que já está no disco: `--local <arquivo> --drive-id <id>` (sem `--drive-id` ele não poderá ser
     rebaixado numa sessão nova).
     Se já existir `transcript.json` do mesmo arquivo (reel-editor/reel-director), passe `--import-transcript <caminho>`.
   - **documento:** baixe pelo conector para `work/entrada/<name>` e rode
     `python3 scripts/run_ingest.py --local "work/entrada/<name>" --drive-id <id> --modified "<modifiedTime>" --licenca propria|terceiros|desconhecida [--fonte "autor, ano, DOI/URL"]`
     (`--drive-id` e `--modified` sempre: dão o mesmo item_id do `01_scan` e permitem reabastecer `work/` depois).
     `--licenca` é obrigatório: material do Gabriel (aulas, atlas dele) = `propria`; artigo/livro = `terceiros`
     com `--fonte`; na dúvida = `desconhecida` (e pergunte).
4. **LER** `work/banco/<item_id>/segment_input.md` (ou `doc_input.md`) **inteiro** — todos os blocos, até o fim —
   e abrir o `contact_sheet.jpg` (Read) para ver enquadramento/qualidade. Em vídeo muito longo, leia em partes,
   mas leia tudo.
5. **Escrever `work/banco/<item_id>/segments.json`** (esquema abaixo) com estes critérios:
   - temas úteis para Reels, específicos (o que se aprende), cobrindo o material inteiro;
   - `gancho_potencial` alto só para frases que param o scroll: **entimema** (conclusão forte com premissa
     implícita que o público completa) ou **antítese** ("todo mundo faz X, só que..."), número surpreendente,
     quebra de crença;
   - `citacao` = palavras EXATAS da transcrição, 6–20 palavras (o validador confere);
   - `publicos` (plantonista, residente, candidato-residencia, concursos-vestibular, geral) só os de fato servidos;
   - `usos`: fala-principal, broll, corte-rapido, citacao-card; notas honestas de qualidade.
6. `python3 scripts/run_ingest.py --finish <item_id>` → valida (ajusta t0/t1 às palavras, confere citações),
   copia para `index/`, refaz `index.md`/`bm25_tokens.json`. Se reprovar, corrija o `segments.json` e rode de novo.
7. **Mover no Drive** o arquivo de `01_entrada` para `02_processado` (`update_file` com o novo parent
   `config.drive.processado`, removendo o antigo). Se o conector não permitir, anote no relatório ao Gabriel
   que ele precisa mover à mão (o `01_scan` não reingere de qualquer forma: compara por `drive_id`).
8. Opcional: subir `index/items/<id>/contact_sheet.jpg` para `04_indice` pelo conector.
9. `git -C ~/reel-polish-src add reel-banco/index && git -C ~/reel-polish-src commit -m "banco: <item_ids>" && git -C ~/reel-polish-src push`.
   - Clone raso (`--depth 1`, como o da reel-polish): rode antes `git -C ~/reel-polish-src fetch --unshallow`.
   - Se o push falhar por acesso/credencial: anexe o repositório com a ferramenta `add_repo`
     (`owner: gabrielquixabeira96-tech`, `repo: editinstagraam`, `access: "push"`) e tente de novo; se continuar
     negado, informe ao Gabriel o motivo exato (o commit local fica pronto).
10. Resumo ao Gabriel: itens ingeridos, nº de segmentos, os 3–5 melhores ganchos (id + citação), pendências.

### Esquema de `segments.json`

```json
{"item_id": "20261020_aula-vias-aereas",
 "segments": [
  {"id": "20261020_aula-vias-aereas#s01", "t0": 125.4, "t1": 161.9,
   "tema": "por que a crico é por punção na criança", "tags": ["via-aerea", "cricotireoidostomia", "pediatria"],
   "tese": "abaixo de ~10 anos a membrana é pequena demais para a técnica cirúrgica",
   "citacao": "na criança pequena a gente faz por punção porque a membrana é minúscula",
   "gancho_potencial": 4, "publicos": ["plantonista", "candidato-residencia"],
   "qualidade_visual": 4, "qualidade_audio": 3, "usos": ["fala-principal", "citacao-card"],
   "notas": "olha para fora da câmera em 150 s"}]}
```
Documento: `"t0": null, "t1": null` e `"ref": {"pagina": 3, "figura": "p3_f1"}` ou `{"secao": 2, "secao_id": "s02"}`.

## Modo 2 — BUSCAR (regra central)

**SEMPRE que o Gabriel pedir um Reel novo, antes de qualquer roteiro ou edição:**

1. Carregar `index/index.md` (catálogo inteiro).
2. Rodar `09_search.py` com **3–6 consultas**: tema literal, sinônimos/termos técnicos, termos leigos, o
   público-alvo (`--publico`), termos adjacentes (causa, complicação, mito associado) e `--uso broll` para imagens.
   ```bash
   python3 scripts/09_search.py "cricotireoidostomia" --show-text
   python3 scripts/09_search.py "via aérea cirúrgica emergência" --publico plantonista --show-text
   python3 scripts/09_search.py "não intubo não ventilo" --uso corte-rapido
   python3 scripts/09_search.py "membrana cricotireóidea anatomia" --tipo doc --uso broll
   ```
3. Ler os segmentos candidatos **na transcrição** (`index/items/<id>/transcript.json`, palavras entre t0 e t1)
   ou no texto do documento — nunca decidir só pela linha do catálogo.
4. Montar a **Ficha de material** e mostrar ao Gabriel:
   - fala principal candidata (segment_id, tempo, citação, por que funciona para o público);
   - B-roll (segmentos `broll`, cenas sem fala, figuras de docs);
   - citações para cards (texto exato, 6–20 palavras);
   - figuras de documentos **com a licença** de cada uma e o crédito a exibir;
   - lacunas: o que o banco NÃO tem para esse Reel.
5. Só então entregar à **reel-director**: para cada clipe escolhido
   `python3 scripts/10_clip.py "<segment_id>" --broll --broll-index /home/claude/reel-director/assets/broll/index.json`
   (copia o mp4/png para `assets/broll/` e registra `{file, source, work, what, license_note, tag, ...}`).
   Para usar um trecho como **fala principal**:
   `python3 scripts/10_clip.py "<segment_id>" --for-editor /home/claude/reel-director/work`
   → gera `work/banco/clips/<item_id>_sNN.mp4` e grava `/home/claude/reel-director/work/<item_id>_sNN/transcript.json`
   (formato reel-editor, tempos relativos ao clipe). Rode a reel-director com o bruto
   `work/banco/clips/<item_id>_sNN.mp4` e `--workdir /home/claude/reel-director/work/<item_id>_sNN`: ela encontra o
   transcript e não retranscreve.

### Reabastecer `work/` (sessão nova)

`work/` não é versionado: numa sessão nova só existe o `index/`. Transcrições, segmentos, thumbs e figuras.json
continuam no índice; só a mídia precisa voltar.
- **Vídeo com `origem.drive_id`:** automático — `10_clip.py` chama `02_fetch.py --item <id>` (link público).
- **Vídeo ingerido com `--local` sem `--drive-id`:** não há de onde rebaixar sozinho. Localize o arquivo no Drive
  (conector `search_files` pelo nome original do `item.json`), e rode
  `python3 scripts/02_fetch.py --file-id <id> --name "<nome_original>" --item-id <item_id>` (ou `--local <arquivo> --item-id <item_id> --drive-id <id>`).
- **Documento (PDF/HTML):** baixe pelo conector (`download_file_content`, id em `item.json → origem.drive_id`;
  artefato: Artifact `read`) para `work/entrada/<nome>` e rode
  `python3 scripts/07_doc_ingest.py --local "work/entrada/<nome>" --item-id <item_id> --licenca <a mesma> [--fonte "..."]`
  — reextrai figuras/capturas em `work/banco/<item_id>/` sem tocar em `segments.json` (os `ref` continuam válidos
  porque a extração é determinística). Depois repita o `10_clip.py`.

## Regras

- **Nunca retranscrever.** `03_transcribe.py` recusa se já existe transcript (no work/ ou no index/). Para
  corrigir um termo, edite só o campo `word`.
- **Nunca subir mídia pesada no git** (só JSON, thumbs 320 px e contact sheets). Mídia mora no Drive e em `work/`.
- **Figuras de terceiros** só com `licenca` registrada; avise o Gabriel e exiba o crédito (`fonte`) na tela.
  `desconhecida` = não usar antes de confirmar com ele.
- **Vídeos longos são ingeridos inteiros** — transcrição, cenas, keyframes e leitura do `segment_input.md` até o fim.
- **Material insuficiente:** diga claramente o que falta (tema, tipo de imagem, público) em vez de inventar,
  esticar um trecho fraco ou gerar imagem por IA para preencher.
- Nunca gerar imagem para substituir ilustração fornecida por ele (regra da reel-director); anatomia nunca por IA.

## Padrão de qualidade do Gabriel (vale para tudo que sair do banco)

- Elementos visuais de alto nível: imagens reais de artigos, cenas reais, animações profissionais — **nada
  infantil**, nada de figura mal feita.
- Legendas e textos de publicação **sem emojis**; tom sério e técnico sem matar o alcance.
- Roteiros e falas no jeito que ele fala: fala corrida com conectores naturais ("porque", "só que", "aí",
  "o que acontece é que") — nunca frases curtas picadas estilo legenda.
- Retórica: **entimema e antíteses** dirigidos a um público específico (plantonista, residente, candidato à
  residência, concursos/vestibular). Ao escolher ganchos no banco, priorize os que já carregam essa estrutura.

## Scripts (todos com `--help`)

`00_check_env` · `01_scan --manifest` · `02_fetch --file-id/--local/--item` · `03_transcribe <id>
[--import-transcript]` · `04_scenes <id>` · `05_keyframes <id>` · `06_segment_input <id>` ·
`06b_validate_segments <id>` · `07_doc_ingest --local --licenca` · `08_index` · `09_search "q"` ·
`10_clip "<seg>" [--broll --broll-index]` · `run_ingest` · `drive_fetch <file_id> <destino> [--expect-size N] [--probe-bytes N]`
(provisório, pode ser substituído; só segue redirecionamentos para https em google.com/*.google.com/*.googleusercontent.com; códigos: 3 = não compartilhado, 4 = HTML inesperado/host não permitido/5xx, 5 = tamanho divergente).
Testes: `python3 tests/test_pipeline.py` (material sintético, sem rede) e `python3 tests/test_memory.py` (1 h de áudio, RSS ≤ 2,5 GB).
