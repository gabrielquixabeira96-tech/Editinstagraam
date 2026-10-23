#!/usr/bin/env python3
"""06b — Valida work/banco/<item_id>/segments.json (escrito pelo Claude) e normaliza.

Vídeo:
  * esquema: id, t0, t1, tema, tags[], tese, citacao, gancho_potencial 0-5, publicos[], qualidade_visual 0-5,
    qualidade_audio 0-5, usos[], notas — valores de publicos/usos dentro das listas permitidas;
  * t0/t1 dentro da duração, t1 > t0; AJUSTA t0 para o start da 1ª palavra que termina depois de t0 e t1
    para o end da última palavra que começa antes de t1 (nunca corta palavra) — só se houver fala no trecho;
  * citacao: confere que as palavras (sem acento/pontuação) aparecem em sequência dentro de [t0, t1]
    (com folga de 2 s); grava citacao_t0/citacao_t1 com os tempos exatos. Divergência = erro;
  * tema/tese/tags/publicos/usos não podem ser vazios; citacao vazia só se usos == ["broll"] ou vídeo sem fala;
  * ids: devem casar ^<item_id>#s\\d{2,}$ e ser únicos; senão renumera tudo (s01..) em ordem de t0 e avisa;
    avisa também se um id já indexado passa a apontar para outro trecho ou desaparece;
  * JSON malformado / segmento que não é objeto -> erro legível.
Documento:
  * t0/t1 devem ser null e `ref` obrigatório ({pagina|secao|figura|...}); citacao precisa existir no texto do doc.
Erros -> código 1 e lista; avisos não bloqueiam. Com sucesso, grava o arquivo normalizado (mesmo caminho) e
marca item.status = "segmentado".

Uso:
  python scripts/06b_validate_segments.py <item_id> [--no-adjust] [--lenient-citacao]
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402

REQ = ["tema", "tags", "tese", "citacao", "gancho_potencial", "publicos", "qualidade_visual", "qualidade_audio",
       "usos", "notas"]


def find_seq(words_norm, quote_norm, lo, hi):
    """procura quote_norm (lista) em words_norm[lo:hi] (índices); devolve (i0, i1) ou None."""
    n = len(quote_norm)
    if n == 0:
        return None
    for i in range(max(0, lo), max(lo, hi - n + 1)):
        if words_norm[i:i + n] == quote_norm:
            return i, i + n - 1
    return None


def doc_text(item_id):
    p = bc.item_dir(item_id) / "doc_text.json"
    if not p.exists():
        p = bc.index_item_dir(item_id) / "doc_text.json"
    if not p.exists():
        return ""
    d = bc.load_json(p)
    return " ".join(x.get("texto", "") for x in d.get("partes", []))


def main():
    ap = argparse.ArgumentParser(description="Valida e normaliza segments.json de um item.")
    ap.add_argument("item_id")
    ap.add_argument("--no-adjust", action="store_true", help="não ajusta t0/t1 às palavras")
    ap.add_argument("--lenient-citacao", action="store_true", help="citação não encontrada vira aviso, não erro")
    args = ap.parse_args()

    bc.set_log_item(args.item_id)
    item = bc.load_item(args.item_id)
    path = bc.item_dir(args.item_id) / "segments.json"
    if not path.exists():
        bc.die(f"{path} não existe — o Claude precisa escrevê-lo a partir de segment_input.md/doc_input.md")
    try:
        raw = bc.load_json(path)
    except (ValueError, UnicodeDecodeError) as e:
        bc.die(f"segments.json não é JSON válido: {e}")
    segs = raw.get("segments", raw) if isinstance(raw, dict) else raw
    if not isinstance(segs, list):
        bc.die("segments.json: esperado {'segments': [...]} ou uma lista")
    bad_items = [k for k, x in enumerate(segs, 1) if not isinstance(x, dict)]
    if bad_items:
        bc.die(f"segments.json: os itens {bad_items} não são objetos JSON ({{...}}); cada segmento deve ser um objeto")
    prev_idx = bc.index_item_dir(args.item_id) / "segments.json"
    prev_ids = {}
    if prev_idx.exists():
        try:
            pd = bc.load_json(prev_idx)
            for x in (pd.get("segments", pd) if isinstance(pd, dict) else pd):
                if isinstance(x, dict) and x.get("id"):
                    prev_ids[x["id"]] = (x.get("t0"), x.get("t1"), x.get("ref"), x.get("tema"))
        except Exception:
            pass
    is_doc = item.get("tipo") == "doc"
    dur = float(item.get("duracao") or 0)
    tr = None if is_doc else bc.load_transcript(args.item_id)
    words = (tr or {}).get("words", [])
    wn = [bc.norm_word(w["word"]) for w in words]
    dtext = bc.norm_text(doc_text(args.item_id)) if is_doc else ""
    dtext_w = " ".join(bc.norm_word(x) for x in dtext.split())

    errors, warns = [], []
    for k, s in enumerate(segs, 1):
        tag = s.get("id") or f"#{k}"
        for f in REQ:
            if f not in s:
                errors.append(f"{tag}: campo '{f}' ausente")
        for f in ("tags", "publicos", "usos"):
            if f in s and not isinstance(s[f], list):
                errors.append(f"{tag}: '{f}' deve ser lista")
                s[f] = []
        for f in ("tema", "tese"):
            if f in s and not str(s.get(f) or "").strip():
                errors.append(f"{tag}: '{f}' vazio")
        for f in ("tema", "tese", "citacao", "notas"):
            if s.get(f) is not None and not isinstance(s[f], str):
                errors.append(f"{tag}: '{f}' deve ser texto")
                s[f] = str(s[f])
        if "publicos" in s and not s["publicos"]:
            errors.append(f"{tag}: 'publicos' vazio (use pelo menos um de {bc.PUBLICOS})")
        if "usos" in s and not s["usos"]:
            errors.append(f"{tag}: 'usos' vazio (use pelo menos um de {bc.USOS})")
        if "tags" in s and not s["tags"]:
            errors.append(f"{tag}: 'tags' vazio")
        if "citacao" in s and not str(s.get("citacao") or "").strip():
            so_broll = s.get("usos") == ["broll"]
            sem_fala = (not is_doc) and not words
            if not (so_broll or sem_fala):
                errors.append(f"{tag}: 'citacao' vazia (só permitida se usos == [\"broll\"] ou vídeo sem fala)")
        for f in ("gancho_potencial", "qualidade_visual", "qualidade_audio"):
            v = s.get(f)
            if v is not None and (not isinstance(v, (int, float)) or not 0 <= v <= 5):
                errors.append(f"{tag}: '{f}' deve ser número 0-5 (recebi {v!r})")
        bad = [p for p in s.get("publicos", []) if p not in bc.PUBLICOS]
        if bad:
            errors.append(f"{tag}: publicos inválidos {bad} (permitidos: {bc.PUBLICOS})")
        bad = [u for u in s.get("usos", []) if u not in bc.USOS]
        if bad:
            errors.append(f"{tag}: usos inválidos {bad} (permitidos: {bc.USOS})")
        s["tags"] = [bc.slugify(t, 40) for t in s.get("tags", []) if str(t).strip()]
        q = s.get("citacao") or ""
        qn = [bc.norm_word(x) for x in q.split() if bc.norm_word(x)]
        if q and not (6 <= len(qn) <= 20):
            warns.append(f"{tag}: citação com {len(qn)} palavras (ideal 6–20)")

        if is_doc:
            if s.get("t0") is not None or s.get("t1") is not None:
                warns.append(f"{tag}: documento — t0/t1 zerados para null")
            s["t0"] = s["t1"] = None
            if not s.get("ref"):
                errors.append(f"{tag}: documento exige 'ref' (ex.: {{\"pagina\": 3}} ou {{\"secao\": 2, \"figura\": \"p3_f1\"}})")
            if q and dtext_w and " ".join(qn) not in dtext_w:
                (warns if args.lenient_citacao else errors).append(f"{tag}: citação não encontrada no texto do documento")
            continue

        try:
            t0, t1 = float(s["t0"]), float(s["t1"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{tag}: t0/t1 numéricos obrigatórios em vídeo")
            continue
        if dur:
            if t0 < 0 or t1 > dur + 0.5:
                errors.append(f"{tag}: [{t0}, {t1}] fora da duração 0–{dur:.2f}")
            t1 = min(t1, dur)
        if t1 <= t0:
            errors.append(f"{tag}: t1 ({t1}) ≤ t0 ({t0})")
            continue
        inside = [i for i, w in enumerate(words) if w["end"] > t0 and w["start"] < t1]
        if inside and not args.no_adjust:
            nt0, nt1 = words[inside[0]]["start"], words[inside[-1]]["end"]
            if abs(nt0 - t0) > 0.01 or abs(nt1 - t1) > 0.01:
                s["t0_original"], s["t1_original"] = round(t0, 3), round(t1, 3)
            t0, t1 = nt0, nt1
        s["t0"], s["t1"] = round(t0, 3), round(t1, 3)
        s["dur"] = round(t1 - t0, 3)
        if q and words:
            lo = next((i for i, w in enumerate(words) if w["end"] > t0 - 2.0), 0)
            hi = next((i for i, w in enumerate(words) if w["start"] > t1 + 2.0), len(words))
            hit = find_seq(wn, qn, lo, hi)
            if hit:
                s["citacao_t0"], s["citacao_t1"] = words[hit[0]]["start"], words[hit[1]]["end"]
                s["citacao_palavras"] = [hit[0], hit[1]]
            else:
                (warns if args.lenient_citacao else errors).append(
                    f"{tag}: citação não encontrada literalmente em [{bc.fmt_t(t0)}–{bc.fmt_t(t1)}]: \"{q[:80]}\"")
        elif q and not words:
            warns.append(f"{tag}: sem transcrição — citação não conferida")
        if "fala-principal" in s.get("usos", []) and s["dur"] < 5:
            warns.append(f"{tag}: fala-principal com só {s['dur']:.1f} s")

    # ids: ^<item_id>#s\d{2,}$, únicos; senão renumera tudo em ordem
    if not is_doc:
        segs.sort(key=lambda s: (s.get("t0") or 0))
    pat = re.compile(r"^" + re.escape(args.item_id) + r"#s\d{2,}$")
    ids = [s.get("id") for s in segs]
    valid = all(isinstance(i, str) and pat.match(i) for i in ids) and len(set(ids)) == len(ids)
    if not valid:
        warns.append("ids fora do padrão <item_id>#sNN (ou repetidos) — renumerados em ordem")
        for k, s in enumerate(segs, 1):
            s["id"] = f"{args.item_id}#s{k:02d}"
    if prev_ids:
        for s in segs:
            old = prev_ids.get(s["id"])
            if old and (old[0], old[1], old[2]) != (s.get("t0"), s.get("t1"), s.get("ref")) and old[3] != s.get("tema"):
                warns.append(f"{s['id']} já estava indexado com outro conteúdo (tema \"{old[3]}\") — "
                             "referências antigas a esse id (fichas, B-roll) passam a apontar para o novo trecho")
        gone = sorted(set(prev_ids) - {s["id"] for s in segs})
        if gone:
            warns.append(f"ids indexados que deixam de existir: {', '.join(gone)}")
    for s in segs:
        s["item_id"] = args.item_id

    for w in warns:
        bc.warn(w)
    if errors:
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        bc.die(f"segments.json reprovado: {len(errors)} erro(s)")
    bc.save_json(path, {"item_id": args.item_id, "validado_em": bc.now_iso(), "segments": segs})
    item["status"] = "segmentado"
    item["n_segmentos"] = len(segs)
    bc.save_item(item)
    bc.log(f"{args.item_id}: segments.json OK ({len(segs)} segmentos, {len(warns)} aviso(s))")


if __name__ == "__main__":
    main()
