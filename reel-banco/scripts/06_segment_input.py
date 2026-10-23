#!/usr/bin/env python3
"""06 — Monta work/banco/<item_id>/segment_input.md: o material que o Claude LÊ para escrever segments.json.

Conteúdo: cabeçalho do item, instruções e esquema de segments.json, e a transcrição INTEIRA em blocos de
~--block s (padrão 60), cada bloco com: tempos, frases com [mm:ss] e #índice de palavra, [pausa], (?) de baixa
confiança, a tabela das cenas que caem no bloco (rosto, nitidez, luma, áudio, notas sugeridas) e as
miniaturas do bloco (caminho absoluto para abrir com Read).
Se o vídeo não tiver fala (sem transcript ou sem palavras), os blocos trazem só cenas e miniaturas — o
material então serve para B-roll.

Uso:
  python scripts/06_segment_input.py <item_id> [--block 60]
Depois: o Claude escreve work/banco/<item_id>/segments.json e roda 06b_validate_segments.py <item_id>.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402

SCHEMA = """```json
{"item_id": "<item_id>",
 "segments": [
  {"id": "<item_id>#s01", "t0": 12.34, "t1": 41.80,
   "tema": "frase curta e específica (o que se aprende)",
   "tags": ["via-aerea", "cricotireoidostomia", "emergencia"],
   "tese": "a afirmação central do trecho, em 1 frase",
   "citacao": "palavras EXATAS da transcrição, 6–20 palavras",
   "gancho_potencial": 0-5,
   "publicos": ["plantonista|residente|candidato-residencia|concursos-vestibular|geral"],
   "qualidade_visual": 0-5, "qualidade_audio": 0-5,
   "usos": ["fala-principal|broll|corte-rapido|citacao-card"],
   "notas": "por que serve, ressalvas (ex.: corte no meio de raciocínio, ruído, olhar fora da câmera)"}
 ]}
```"""

GUIDE = """## Como escrever `segments.json` (critérios)

- **Cubra o vídeo inteiro**, do começo ao fim — não só os primeiros minutos. Trechos sem valor (logística,
  silêncio, conversa lateral) simplesmente não viram segmento.
- Segmento = unidade reaproveitável num Reel: **8–60 s** para `fala-principal`, **2–8 s** para `corte-rapido`/`broll`.
  Comece e termine em fronteira de frase (o validador ajusta t0/t1 às palavras).
- `tema`: específico ("por que a cricotireoidostomia é por punção na criança"), nunca genérico ("vias aéreas").
- `tese`: a afirmação que o trecho defende — base do entimema (premissa implícita + conclusão).
- `citacao`: copie as **palavras exatas** (6–20) de dentro de [t0, t1]; o validador confere e acusa divergência.
- `gancho_potencial` 0–5: 5 = frase que para o scroll sozinha (contraste/antítese, número surpreendente,
  quebra de crença, "todo mundo faz X, só que..."); 3 = boa explicação sem frase de impacto; 0–1 = só contexto.
- `publicos`: para quem o trecho é mais forte (plantonista, residente, candidato-residencia,
  concursos-vestibular, geral). Pode ser mais de um; não marque todos por padrão.
- `qualidade_visual`/`qualidade_audio` 0–5: parta de `sug_visual`/`sug_audio` das cenas e corrija olhando as
  miniaturas (rosto cortado, contraluz, tremido, ruído, eco).
- `usos`: `fala-principal` (ele falando bem, pode carregar o Reel), `broll` (imagem que ilustra sem depender da
  fala: procedimento, tela, quadro, ambiente), `corte-rapido` (frase curta e forte para montagem), `citacao-card`
  (frase que funciona escrita na tela).
- `tags`: 3–8, minúsculas, com hífen, sem acento é opcional (a busca ignora acentos). Inclua sinônimos úteis
  (ex.: "intubacao", "iot", "via-aerea-dificil").
- Não invente: se o trecho é fraco, dê nota baixa; se o vídeo não tem nada útil, escreva `"segments": []` e explique em `notas` do item.
"""


def main():
    ap = argparse.ArgumentParser(description="Gera segment_input.md (transcrição em blocos + cenas + miniaturas).")
    ap.add_argument("item_id")
    ap.add_argument("--block", type=float, default=60.0, help="tamanho do bloco em s")
    args = ap.parse_args()

    bc.set_log_item(args.item_id)
    item = bc.load_item(args.item_id)
    d = bc.item_dir(args.item_id)
    tr = bc.load_transcript(args.item_id) or {"words": []}
    words = tr.get("words", [])
    scenes = bc.load_json(d / "scenes.json")["scenes"] if (d / "scenes.json").exists() else []
    kfs = bc.load_json(d / "keyframes.json") if (d / "keyframes.json").exists() else []
    dur = float(item.get("duracao") or (scenes[-1]["t1"] if scenes else (words[-1]["end"] if words else 0)))
    idx = bc.index_item_dir(args.item_id)

    L = [f"# Material para segmentação — `{args.item_id}`", "",
         f"- Arquivo: **{item.get('origem', {}).get('nome_original', item.get('nome'))}** — tipo vídeo — "
         f"duração **{bc.fmt_t(dur)}** — {item.get('largura')}×{item.get('altura')} — {len(words)} palavras — "
         f"{len(scenes)} cenas/blocos — {len(kfs)} quadros-chave",
         f"- Contact sheet: `{idx / 'contact_sheet.jpg'}`" + (f" (+{len(item.get('contact_sheets', [])) - 1} folhas)"
                                                             if len(item.get("contact_sheets", [])) > 1 else ""),
         f"- Transcrição completa (tempos exatos por palavra): `{d / 'transcript.json'}`",
         f"- Escreva: `{d / 'segments.json'}` e depois rode `python scripts/06b_validate_segments.py {args.item_id}`",
         "", GUIDE, "## Esquema", "", SCHEMA.replace("<item_id>", args.item_id), ""]
    if not words:
        L += ["> **Sem fala transcrita.** Este material serve principalmente como B-roll: segmente pelas cenas.", ""]

    L.append("## Transcrição em blocos")
    wi = 0
    t = 0.0
    b = 0
    while t < dur or (b == 0):
        b += 1
        t1 = min(dur, t + args.block) if dur else t + args.block
        L += ["", f"### Bloco {b} — [{bc.fmt_t(t)}–{bc.fmt_t(t1)}]", ""]
        phrase, p0, i0, prev_end = [], None, None, None
        lines = []
        while wi < len(words) and words[wi]["start"] < t1:
            w = words[wi]
            if prev_end is not None and w["start"] - prev_end > 0.6:
                if phrase:
                    lines.append(f"[{bc.fmt_t(p0)}] #{i0} " + " ".join(phrase))
                    phrase = []
                lines.append(f"  [pausa {w['start'] - prev_end:.1f} s]")
            if not phrase:
                p0, i0 = w["start"], wi
            tok = w["word"].strip() + ("(?)" if w.get("prob", 1) < 0.5 else "")
            phrase.append(tok)
            prev_end = w["end"]
            if tok.rstrip("(?)").endswith((".", "?", "!")) or len(phrase) >= 18:
                lines.append(f"[{bc.fmt_t(p0)}] #{i0} " + " ".join(phrase))
                phrase = []
            wi += 1
        if phrase:
            lines.append(f"[{bc.fmt_t(p0)}] #{i0} " + " ".join(phrase))
        L += lines or ["_(sem fala neste bloco)_"]
        sc = [s for s in scenes if s["t1"] > t and s["t0"] < t1]
        if sc:
            L += ["", "| cena | início | fim | rosto | tam | nitidez | luma | áudio dB | silêncio | sug_vis | sug_aud |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
            for s in sc:
                L.append(f"| c{s['n']} ({s['corte']}) | {bc.fmt_t(s['t0'])} | {bc.fmt_t(s['t1'])} | "
                         f"{'—' if s.get('rosto') is None else s['rosto']} | {s.get('rosto_tam') or '—'} | "
                         f"{s.get('nitidez')} | {s.get('luma')} | {s.get('audio_db')} | {s.get('silencio')} | "
                         f"{s.get('sug_visual')} | {s.get('sug_audio')} |")
        ks = [k for k in kfs if t <= k["t"] < t1]
        if ks:
            L += ["", "Miniaturas: " + " · ".join(f"{k['k']} {bc.fmt_t(k['t'])} `{idx / k.get('thumb', 'thumbs/' + k['arquivo'])}`"
                                                    for k in ks)]
        t = t1
        if not dur:
            break
    out = d / "segment_input.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    item["status"] = "aguardando-segmentos"
    bc.save_item(item)
    bc.log(f"{args.item_id}: OK {b} blocos -> {out}")
    print(f"LER={out}")
    print(f"ESCREVER={d / 'segments.json'}")


if __name__ == "__main__":
    main()
