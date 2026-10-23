#!/usr/bin/env python3
"""08 — Reconstrói o índice do banco a partir de index/items/*/ (item.json + segments.json).

Saídas (todas em index/, versionadas no git):
  index.json  {"gerado_em", "n_itens", "n_segmentos", "itens": [item resumido + "segmentos": [...]]}
  index.md    catálogo legível: por item, 1 linha por segmento (id, origem, tempo/ref, tema, tags, gancho,
              públicos, usos) — o Claude carrega este arquivo antes de qualquer Reel novo
  bm25_tokens.json  tokens por segmento (tema+tese+citação+tags+notas + transcrição do trecho; pt sem acentos);
              o 09_search.py reconstrói o BM25Okapi a partir dele (JSON puro: sem pickle no repositório)

Só entram itens com segments.json no índice (index/items/<id>/segments.json, copiado no --finish).
Itens sem segmentos aparecem em index.md como "pendente".

Uso:
  python scripts/08_index.py [--no-transcript-text]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402


def seg_transcript_text(tr, t0, t1, limit=400):
    if not tr or t0 is None:
        return ""
    ws = [w["word"] for w in tr.get("words", []) if w["end"] > t0 and w["start"] < t1]
    return " ".join(ws[:limit])


def where(seg):
    if seg.get("t0") is not None:
        return f"{bc.fmt_t(seg['t0'])}–{bc.fmt_t(seg['t1'])} ({seg.get('dur', seg['t1'] - seg['t0']):.0f} s)"
    r = seg.get("ref") or {}
    parts = []
    if "pagina" in r:
        parts.append(f"p.{r['pagina']}")
    if "secao" in r:
        parts.append(f"seção {r['secao']}")
    if "figura" in r:
        parts.append(f"fig {r['figura']}")
    return " ".join(parts) or "—"


def main():
    ap = argparse.ArgumentParser(description="Reconstrói index.json, index.md e bm25_tokens.json.")
    ap.add_argument("--no-transcript-text", action="store_true", help="não inclui o texto da fala no BM25")
    args = ap.parse_args()

    root = bc.index_root()
    items_dir = root / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    itens, docs_tokens, docs_ids, pend = [], [], [], []
    for ip in sorted(items_dir.glob("*/item.json")):
        it = bc.load_json(ip)
        iid = it["id"]
        sp = ip.parent / "segments.json"
        if not sp.exists():
            pend.append(it)
            continue
        sd = bc.load_json(sp)
        segs = sd.get("segments", sd) if isinstance(sd, dict) else sd
        tr = None
        if not args.no_transcript_text and (ip.parent / "transcript.json").exists():
            tr = bc.load_json(ip.parent / "transcript.json")
        resumo = {k: it.get(k) for k in ("id", "nome", "tipo", "subtipo", "duracao", "largura", "altura", "licenca",
                                         "fonte", "status", "paginas", "secoes", "n_figuras", "contact_sheets")}
        resumo["origem"] = {k: it.get("origem", {}).get(k) for k in ("drive_id", "nome_original")}
        resumo["segmentos"] = []
        for s in segs:
            s = dict(s)
            s["item_id"] = iid
            s["tipo"] = it.get("tipo")
            s["licenca"] = s.get("licenca") or it.get("licenca")
            resumo["segmentos"].append(s)
            text = " ".join([s.get("tema", ""), s.get("tese", ""), s.get("citacao", ""), " ".join(s.get("tags", [])),
                             " ".join(t.replace("-", " ") for t in s.get("tags", [])), s.get("notas", ""),
                             it.get("nome", ""), seg_transcript_text(tr, s.get("t0"), s.get("t1"))])
            # campos curtos pesam mais (repetição = peso no BM25)
            boost = " ".join([s.get("tema", ""), " ".join(t.replace("-", " ") for t in s.get("tags", []))] * 2)
            docs_tokens.append(bc.tokenize(text + " " + boost))
            docs_ids.append(s["id"])
        itens.append(resumo)

    nseg = sum(len(i["segmentos"]) for i in itens)
    bc.save_json(root / "index.json", {"gerado_em": bc.now_iso(), "n_itens": len(itens), "n_segmentos": nseg,
                                       "itens": itens, "pendentes": [p["id"] for p in pend]})

    L = ["# Banco de material para Reels — catálogo", "",
         f"Gerado em {bc.now_iso()} · {len(itens)} item(ns) · {nseg} segmento(s)"
         + (f" · {len(pend)} pendente(s) de segmentação" if pend else ""), "",
         "Legenda: G = gancho_potencial (0–5) · V/A = qualidade visual/áudio · usos: FP fala-principal, BR broll, "
         "CR corte-rapido, CC citacao-card · públicos: PL plantonista, RE residente, CA candidato-residencia, "
         "CV concursos-vestibular, GE geral", ""]
    ab_u = {"fala-principal": "FP", "broll": "BR", "corte-rapido": "CR", "citacao-card": "CC"}
    ab_p = {"plantonista": "PL", "residente": "RE", "candidato-residencia": "CA", "concursos-vestibular": "CV", "geral": "GE"}
    for it in itens:
        dur = f" · {bc.fmt_t(it['duracao'])}" if it.get("duracao") else ""
        lic = f" · licença {it.get('licenca')}" if it.get("licenca") else ""
        L += [f"## {it['id']} — {it.get('nome')} ({it.get('subtipo') or it.get('tipo')}{dur}{lic})", ""]
        if it.get("fonte"):
            L += [f"Fonte: {it['fonte']}", ""]
        L += ["| segmento | onde | tema | tags | G | V/A | públicos | usos |", "|---|---|---|---|---|---|---|---|"]
        for s in it["segmentos"]:
            L.append("| {id} | {w} | {tema} | {tags} | {g} | {v}/{a} | {p} | {u} |".format(
                id=s["id"], w=where(s), tema=(s.get("tema") or "").replace("|", "/"),
                tags=", ".join(s.get("tags", [])), g=s.get("gancho_potencial"),
                v=s.get("qualidade_visual"), a=s.get("qualidade_audio"),
                p=" ".join(ab_p.get(x, x) for x in s.get("publicos", [])),
                u=" ".join(ab_u.get(x, x) for x in s.get("usos", []))))
        L.append("")
    if pend:
        L += ["## Pendentes (sem segments.json no índice)", ""]
        L += [f"- {p['id']} — {p.get('nome')} ({p.get('tipo')}, status {p.get('status')})" for p in pend]
        L.append("")
    (root / "index.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # só dados (JSON) no repositório: o BM25Okapi é reconstruído na busca (nada de pickle versionado)
    bc.save_json(root / "bm25_tokens.json", {"versao": 2, "gerado_em": bc.now_iso(), "ids": docs_ids,
                                             "tokens": docs_tokens}, compact=True)
    old = root / "bm25.pkl"
    if old.exists():
        old.unlink()
    bc.log(f"índice: {len(itens)} item(ns), {nseg} segmento(s), {len(pend)} pendente(s) -> {root}")


if __name__ == "__main__":
    main()
