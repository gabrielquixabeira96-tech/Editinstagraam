#!/usr/bin/env python3
"""09 — Busca no banco: BM25 (reconstruído de index/bm25_tokens.json) + bônus por tags, público, uso e gancho.

Pontuação final = BM25 normalizado (0–1 pelo melhor resultado)
                + 0,25 por tag pedida (--tags) presente no segmento (ou termo da consulta que é tag exata)
                + 0,20 se o segmento atende o --publico
                + 0,15 se atende o --uso
                + 0,03 × gancho_potencial
Filtros duros: --tipo video|doc, --min-gancho, --licenca. Sem consulta (""), lista por tags/filtros.

Uso:
  python scripts/09_search.py "cricotireoidostomia" [--tags via-aerea,emergencia] [--tipo video]
         [--publico plantonista] [--uso broll] [--min-gancho 3] [--top 15] [--json] [--show-text]
Saída: tabela legível (rank, score, segmento, onde, tema, tags, G, usos) ou JSON (--json) com os segmentos
completos (+ caminho da mídia em work/ se presente). O índice precisa existir (08_index.py).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402


def load_index():
    root = bc.index_root()
    if not (root / "index.json").exists() or not (root / "bm25_tokens.json").exists():
        bc.die("índice ausente — rode scripts/08_index.py")
    idx = bc.load_json(root / "index.json")
    bm = bc.load_json(root / "bm25_tokens.json")
    if not isinstance(bm.get("ids"), list) or not isinstance(bm.get("tokens"), list) or len(bm["ids"]) != len(bm["tokens"]):
        bc.die("bm25_tokens.json inválido — rode scripts/08_index.py")
    bm["bm25"] = None
    if bm["tokens"]:
        from rank_bm25 import BM25Okapi
        bm["bm25"] = BM25Okapi([[str(t) for t in toks] for toks in bm["tokens"]])
    segs = {}
    for it in idx.get("itens", []):
        for s in it.get("segmentos", []):
            segs[s["id"]] = s
    return idx, bm, segs


def where(s):
    if s.get("t0") is not None:
        return f"{bc.fmt_t(s['t0'])}–{bc.fmt_t(s['t1'])}"
    r = s.get("ref") or {}
    return " ".join(f"{k[:3]}={v}" for k, v in r.items()) or "—"


def search(query, tags=(), tipo=None, publico=None, uso=None, min_gancho=None, licenca=None, top=15):
    idx, bm, segs = load_index()
    ids = bm.get("ids", [])
    qtok = bc.tokenize(query or "")
    raw = [0.0] * len(ids)
    if qtok and bm.get("bm25") is not None:
        raw = list(bm["bm25"].get_scores(qtok))
        # BM25Okapi pode dar idf negativo em corpus minúsculo: piso em 0 + sobreposição simples
        for i, toks in enumerate(bm["tokens"]):
            ov = len(set(qtok) & set(toks)) / max(1, len(set(qtok)))
            raw[i] = max(raw[i], 0.0) + ov
    mx = max(raw) if raw and max(raw) > 0 else 1.0
    want_tags = {bc.slugify(t, 40) for t in tags if t}
    qtags = {bc.slugify(t, 40) for t in (query or "").split()} | {bc.slugify(query or "x", 40)}
    res = []
    for i, sid in enumerate(ids):
        s = segs.get(sid)
        if not s:
            continue
        if tipo and s.get("tipo") != tipo:
            continue
        if min_gancho is not None and (s.get("gancho_potencial") or 0) < min_gancho:
            continue
        if licenca and s.get("licenca") != licenca:
            continue
        stags = set(s.get("tags", []))
        score = raw[i] / mx
        hit_tags = (want_tags & stags) | (qtags & stags)
        score += 0.25 * len(hit_tags)
        if publico and publico in s.get("publicos", []):
            score += 0.20
        if uso and uso in s.get("usos", []):
            score += 0.15
        score += 0.03 * (s.get("gancho_potencial") or 0)
        if not qtok and not want_tags:
            pass
        elif raw[i] <= 0 and not hit_tags:
            continue
        res.append((round(score, 3), s))
    res.sort(key=lambda x: -x[0])
    return res[:top]


def main():
    ap = argparse.ArgumentParser(description="Busca segmentos no banco (BM25 + tags/público).")
    ap.add_argument("query", nargs="?", default="", help="consulta em linguagem natural (acentos são ignorados)")
    ap.add_argument("--tags", default="", help="tags separadas por vírgula (bônus)")
    ap.add_argument("--tipo", choices=["video", "doc"])
    ap.add_argument("--publico", choices=bc.PUBLICOS)
    ap.add_argument("--uso", choices=bc.USOS)
    ap.add_argument("--min-gancho", type=float)
    ap.add_argument("--licenca", choices=bc.LICENCAS)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-text", action="store_true", help="mostra tese e citação de cada resultado")
    args = ap.parse_args()

    res = search(args.query, [t.strip() for t in args.tags.split(",")], args.tipo, args.publico, args.uso,
                 args.min_gancho, args.licenca, args.top)
    if args.json:
        out = []
        for sc, s in res:
            src = bc.find_source(s["item_id"])
            out.append({"score": sc, **s, "midia_local": str(src) if src else None})
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if not res:
        print(f'Nenhum resultado para "{args.query}" (filtros: tags={args.tags or "-"}, tipo={args.tipo or "-"}, '
              f'publico={args.publico or "-"}). Tente sinônimos ou termos adjacentes.')
        return
    print(f'{len(res)} resultado(s) para "{args.query}"')
    print(f"{'#':>2} {'score':>5}  {'segmento':<42} {'onde':<19} {'G':>3}  {'usos':<28} tema  [tags]")
    for k, (sc, s) in enumerate(res, 1):
        print(f"{k:>2} {sc:5.2f}  {s['id']:<42} {where(s):<19} {s.get('gancho_potencial', 0):>3}  "
              f"{','.join(s.get('usos', [])):<28} {s.get('tema', '')}  [{', '.join(s.get('tags', []))}]")
        if args.show_text:
            print(f"      tese: {s.get('tese', '')}")
            print(f"      citação: \"{s.get('citacao', '')}\"  | públicos: {', '.join(s.get('publicos', []))}"
                  + (f" | licença: {s.get('licenca')}" if s.get("licenca") and s.get("licenca") != "propria" else ""))


if __name__ == "__main__":
    main()
