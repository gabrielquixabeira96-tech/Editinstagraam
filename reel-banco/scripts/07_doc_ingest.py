#!/usr/bin/env python3
"""07 — Ingestão de documento (PDF ou HTML/artefato) já baixado pelo Claude (conector Drive ou Artifact read).

PDF (PyMuPDF):
  * texto por página -> doc_text.json {"partes": [{"ref": {"pagina": n}, "titulo", "texto"}]}
  * figuras: imagens embutidas >= --min-fig px (lado menor) extraídas para work/banco/<id>/figuras/pN_fK.png,
    com página, bbox e LEGENDA PRÓXIMA (bloco de texto logo abaixo/acima que começa com Fig/Figura/Figure/
    Tabela/Table/Quadro, senão o bloco mais próximo); render da página inteira (pN.jpg, 110 dpi) para contexto.
HTML (Playwright + Chromium, viewport 540x960, deviceScaleFactor 2):
  * seções = cada h1/h2/h3 (ou <section>/<article>) com o texto até o próximo título;
  * captura de tela de cada seção (work/banco/<id>/secoes/sNN.png) e da página inteira (página_inteira.png).
    Se o Chromium falhar, extrai só o texto (aviso).

Campo obrigatório --licenca propria|terceiros|desconhecida (vai para item.json e para cada figura). Figuras de
terceiros só podem ir para um Reel com a licença registrada e aviso de fonte.

Saídas: item.json, doc_text.json, figuras.json, doc_input.md (para o Claude escrever segments.json com
t0/t1 nulos e `ref`), miniaturas 320 px em index/items/<id>/thumbs/ e contact_sheet.jpg (figuras/seções).

Uso:
  python scripts/07_doc_ingest.py --local artigo.pdf --licenca terceiros [--fonte "DOI 10.xxxx"] [--drive-id ID]
  python scripts/07_doc_ingest.py --local atlas.html --licenca propria [--name "Atlas cricotireoidostomia"]
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402

CAP_RE = re.compile(r"^\s*(fig\.?|figura|figure|tabela|table|quadro|gr[aá]fico|imagem)\s*\d*", re.I)


def thumb(src: Path, dest: Path, w=320):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    h = max(1, int(round(im.height * w / im.width)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.resize((w, h), Image.LANCZOS).save(dest, quality=82)


def sheet(item_id, entries, name="contact_sheet.jpg", cols=4, cell_h=420):
    """entries: [(label, thumb_path)]"""
    from PIL import Image, ImageDraw, ImageFont
    entries = [(l, p) for l, p in entries if Path(p).exists()][:48]
    if not entries:
        return None
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    pad, head, cw = 6, 28, 320
    rows = (len(entries) + cols - 1) // cols
    sh = Image.new("RGB", (min(cols, len(entries)) * (cw + pad) + pad, rows * (cell_h + head + pad) + pad), (16, 18, 22))
    dr = ImageDraw.Draw(sh)
    for j, (label, p) in enumerate(entries):
        im = Image.open(p).convert("RGB")
        if im.height > cell_h:
            im = im.crop((0, 0, im.width, cell_h))
        x = pad + (j % cols) * (cw + pad)
        y = pad + (j // cols) * (cell_h + head + pad)
        sh.paste(im, (x, y + head))
        dr.text((x + 4, y + 4), label[:34], fill=(57, 229, 140), font=font)
    out = bc.index_item_dir(item_id) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    sh.save(out, quality=80)
    return out


# --------------------------------------------------------------------------- PDF
def ingest_pdf(item, src: Path, min_fig: int, licenca: str):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore
    d = bc.item_dir(item["id"])
    fdir = d / "figuras"
    if fdir.exists():
        shutil.rmtree(fdir)
    fdir.mkdir(parents=True)
    tdir = bc.index_item_dir(item["id"]) / "thumbs"
    if tdir.exists():
        shutil.rmtree(tdir)
    doc = fitz.open(str(src))
    meta = doc.metadata or {}
    partes, figs = [], []
    for pno in range(doc.page_count):
        page = doc[pno]
        n = pno + 1
        text = page.get_text("text").strip()
        blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
        first = next((b[4].strip().split("\n")[0] for b in blocks), "")
        partes.append({"ref": {"pagina": n}, "titulo": first[:120], "texto": re.sub(r"[ \t]+", " ", text)})
        pg = fdir / f"p{n}.jpg"
        page.get_pixmap(dpi=110).save(str(pg))
        k = 0
        seen = set()
        for info in page.get_image_info(xrefs=True):
            xref = info.get("xref")
            if not xref or xref in seen:
                continue
            seen.add(xref)
            w, h = info.get("width", 0), info.get("height", 0)
            if min(w, h) < min_fig:
                continue
            bbox = fitz.Rect(info["bbox"])
            if bbox.width < 40 or bbox.height < 40:
                continue
            k += 1
            fid = f"p{n}_f{k}"
            out = fdir / f"{fid}.png"
            try:
                pix = page.get_pixmap(clip=bbox, dpi=200)
                pix.save(str(out))
            except Exception:
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    pix.save(str(out))
                except Exception as e:
                    bc.warn(f"figura {fid}: não extraída ({e})")
                    continue
            # legenda próxima
            cap, best = "", 1e9
            for b in blocks:
                br = fitz.Rect(b[:4])
                txt = b[4].strip().replace("\n", " ")
                below = br.y0 - bbox.y1
                above = bbox.y0 - br.y1
                dist = below if below >= -5 else (above if above >= -5 else 1e9)
                if dist > 140:
                    continue
                score = dist - (200 if CAP_RE.match(txt) else 0)
                if score < best:
                    best, cap = score, txt
            thumb(out, tdir / f"{fid}.jpg")
            figs.append({"id": fid, "pagina": n, "arquivo": f"figuras/{fid}.png", "thumb": f"thumbs/{fid}.jpg",
                         "bbox": [round(v, 1) for v in bbox], "largura_px": w, "altura_px": h,
                         "legenda": cap[:400], "licenca": licenca})
        thumb(pg, tdir / f"p{n}.jpg")
    item.update({"subtipo": "pdf", "paginas": doc.page_count, "titulo_doc": meta.get("title") or None,
                 "autor_doc": meta.get("author") or None})
    entries = [(f"{f['id']}  p{f['pagina']}", bc.index_item_dir(item["id"]) / f["thumb"]) for f in figs]
    if len(entries) < 4:
        entries += [(f"página {p['ref']['pagina']}", tdir / f"p{p['ref']['pagina']}.jpg") for p in partes[:12]]
    return partes, figs, entries


# --------------------------------------------------------------------------- HTML
JS_SECTIONS = r"""
() => {
  const hs = Array.from(document.querySelectorAll('h1,h2,h3'));
  let els = hs;
  if (els.length === 0) els = Array.from(document.querySelectorAll('section,article'));
  const out = [];
  els.forEach((h, i) => {
    const next = els[i+1];
    const r = h.getBoundingClientRect();
    const top = r.top + window.scrollY;
    let bottom = next ? next.getBoundingClientRect().top + window.scrollY : document.documentElement.scrollHeight;
    let txt = '';
    if (['H1','H2','H3'].includes(h.tagName)) {
      let n = h.nextElementSibling; const parts = [];
      while (n && !(['H1','H2','H3'].includes(n.tagName))) { parts.push(n.innerText || ''); n = n.nextElementSibling; }
      txt = parts.join('\n');
      if (!txt.trim() && h.parentElement) txt = (h.parentElement.innerText || '').replace(h.innerText, '');
    } else { txt = h.innerText || ''; }
    out.push({titulo: (h.innerText || '').trim().slice(0, 160), texto: txt.trim(), top: top, bottom: bottom,
              nivel: h.tagName});
  });
  return {titulo: document.title || '', altura: document.documentElement.scrollHeight,
          texto_total: document.body ? document.body.innerText : '', secoes: out};
}
"""


def ingest_html(item, src: Path, licenca: str):
    d = bc.item_dir(item["id"])
    sdir = d / "secoes"
    if sdir.exists():
        shutil.rmtree(sdir)
    sdir.mkdir(parents=True)
    tdir = bc.index_item_dir(item["id"]) / "thumbs"
    if tdir.exists():
        shutil.rmtree(tdir)
    partes, figs, entries = [], [], []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch()
            pg = br.new_page(viewport={"width": 540, "height": 960}, device_scale_factor=2)
            pg.goto(src.resolve().as_uri(), wait_until="load", timeout=60000)
            pg.wait_for_timeout(800)
            info = pg.evaluate(JS_SECTIONS)
            full_h = int(info.get("altura") or 960)
            pg.screenshot(path=str(sdir / "pagina_inteira.png"), full_page=True)
            secs = info.get("secoes") or []
            if not secs:
                secs = [{"titulo": info.get("titulo") or item["nome"], "texto": info.get("texto_total", ""),
                         "top": 0, "bottom": full_h, "nivel": "BODY"}]
            for i, s in enumerate(secs, 1):
                sid = f"s{i:02d}"
                top = max(0, int(s["top"]) - 8)
                h = int(min(max(240, s["bottom"] - s["top"] + 8), 1920))
                if top >= full_h:
                    continue
                h = min(h, full_h - top)
                out = sdir / f"{sid}.png"
                try:
                    pg.screenshot(path=str(out), full_page=True, clip={"x": 0, "y": top, "width": 540, "height": h})
                    thumb(out, tdir / f"{sid}.jpg")
                except Exception as e:
                    bc.warn(f"captura da seção {sid} falhou: {e}")
                    out = None
                partes.append({"ref": {"secao": i, "secao_id": sid}, "titulo": s["titulo"],
                               "texto": re.sub(r"\n{3,}", "\n\n", s["texto"] or "")[:20000], "nivel": s.get("nivel")})
                if out:
                    figs.append({"id": sid, "secao": i, "arquivo": f"secoes/{sid}.png", "thumb": f"thumbs/{sid}.jpg",
                                 "legenda": s["titulo"], "licenca": licenca, "captura": "540x960@2x"})
                    entries.append((f"{sid} {s['titulo'][:24]}", tdir / f"{sid}.jpg"))
            item["titulo_doc"] = info.get("titulo") or None
            br.close()
    except Exception as e:
        bc.warn(f"Playwright/Chromium indisponível ({str(e)[:200]}) — só texto, sem capturas")
        html = src.read_text(encoding="utf-8", errors="ignore")
        chunks = re.split(r"<h[1-3][^>]*>", html, flags=re.I)
        for i, ch in enumerate(chunks, 1):
            title = re.sub(r"<[^>]+>", "", ch.split("</h", 1)[0]).strip()[:160]
            text = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", ch, flags=re.S | re.I))
            partes.append({"ref": {"secao": i, "secao_id": f"s{i:02d}"}, "titulo": title, "texto": text.strip()[:20000]})
    item.update({"subtipo": "html", "secoes": len(partes)})
    return partes, figs, entries


def write_doc_input(item, partes, figs):
    d = bc.item_dir(item["id"])
    idx = bc.index_item_dir(item["id"])
    L = [f"# Material para segmentação — documento `{item['id']}`", "",
         f"- Arquivo: **{item['origem'].get('nome_original', item['nome'])}** — {item['subtipo'].upper()} — "
         f"{len(partes)} {'páginas' if item['subtipo'] == 'pdf' else 'seções'} — {len(figs)} figuras/capturas",
         f"- **Licença registrada: `{item['licenca']}`**" + (f" — fonte: {item['fonte']}" if item.get("fonte") else ""),
         f"- Contact sheet: `{idx / 'contact_sheet.jpg'}`",
         f"- Escreva: `{d / 'segments.json'}` (mesmo esquema dos vídeos, com `\"t0\": null, \"t1\": null` e `ref`) e rode "
         f"`python scripts/06b_validate_segments.py {item['id']}`", "",
         "## Como segmentar um documento", "",
         "- 1 segmento = 1 ideia reaproveitável: um dado, uma frase citável, uma figura que explica, uma seção de atlas.",
         "- `ref` obrigatório: `{\"pagina\": 4}`, `{\"pagina\": 4, \"figura\": \"p4_f1\"}` ou `{\"secao\": 3, \"secao_id\": \"s03\"}`.",
         "- `citacao`: palavras EXATAS do texto (6–20). `usos`: normalmente `citacao-card` e/ou `broll` (figura/captura).",
         "- Figuras de **terceiros**: escreva em `notas` a fonte completa e que o uso exige crédito na tela; se `desconhecida`, "
         "não marque `broll` sem antes confirmar a licença com o usuário.",
         "- `qualidade_visual`: legibilidade da figura/captura em 9:16 (texto pequeno = nota baixa). `qualidade_audio`: 0.",
         "", "## Esquema", "",
         '```json\n{"item_id": "%s", "segments": [{"id": "%s#s01", "t0": null, "t1": null, "ref": {"pagina": 2, "figura": "p2_f1"},'
         ' "tema": "...", "tags": ["..."], "tese": "...", "citacao": "palavras exatas", "gancho_potencial": 3,'
         ' "publicos": ["residente"], "qualidade_visual": 4, "qualidade_audio": 0, "usos": ["citacao-card", "broll"],'
         ' "notas": "..."}]}\n```' % (item["id"], item["id"]), ""]
    if figs:
        L += ["## Figuras / capturas", "", "| id | onde | legenda próxima | miniatura |", "|---|---|---|---|"]
        for f in figs:
            onde = f"p{f['pagina']}" if "pagina" in f else f"seção {f.get('secao')}"
            L.append(f"| {f['id']} | {onde} | {(f.get('legenda') or '').replace('|', '/')[:160]} | `{idx / f['thumb']}` |")
        L.append("")
    L.append("## Texto")
    for p in partes:
        ref = p["ref"]
        lab = f"Página {ref['pagina']}" if "pagina" in ref else f"Seção {ref['secao']} — {p.get('titulo', '')}"
        L += ["", f"### {lab}", "", (p["texto"] or "_(sem texto)_")[:12000]]
    out = d / "doc_input.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="Ingestão de PDF/HTML: texto, figuras/capturas, licença e doc_input.md.")
    ap.add_argument("--local", required=True, help="caminho do .pdf ou .html")
    ap.add_argument("--licenca", required=True, choices=bc.LICENCAS, help="propria | terceiros | desconhecida (obrigatório)")
    ap.add_argument("--fonte", help="referência/DOI/URL/autor do material (recomendado para terceiros)")
    ap.add_argument("--name", help="nome do item (padrão: nome do arquivo)")
    ap.add_argument("--drive-id", help="id do arquivo no Drive (para mover para 02_processado depois)")
    ap.add_argument("--modified", help="modifiedTime ISO (define a data do item_id)")
    ap.add_argument("--item-id")
    ap.add_argument("--min-fig", type=int, default=120, help="lado menor mínimo (px) para extrair figura do PDF")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    src0 = Path(os.path.expanduser(args.local)).resolve()
    if not src0.exists():
        bc.die(f"arquivo não encontrado: {src0}")
    ext = src0.suffix.lower()
    if ext not in (".pdf", ".html", ".htm"):
        bc.die(f"tipo não suportado: {ext} (use .pdf ou .html)")
    name = args.name or src0.name
    item_id = args.item_id or bc.make_item_id(name, args.modified or src0.stat().st_mtime)
    bc.set_log_item(item_id)
    d = bc.item_dir(item_id)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"source{'.html' if ext == '.htm' else ext}"
    if src0 != dest.resolve():
        shutil.copy2(src0, dest)
    st = bc.State(item_id)
    signature = bc.sig([bc.sha256_file(dest), args.licenca, args.fonte, args.min_fig, 2])
    if not args.force and st.fresh("doc_ingest", signature, [d / "doc_input.md"]):
        bc.log(f"{item_id}: documento já ingerido (cache)")
        print(f"ITEM_ID={item_id}")
        print(f"LER={d / 'doc_input.md'}")
        return
    prev = bc.load_item(item_id, required=False) or {}
    item = {**prev, "id": item_id, "nome": Path(name).stem, "tipo": "doc",
            "origem": {**prev.get("origem", {}), **{k: v for k, v in {
                "local": str(src0), "nome_original": src0.name if not args.name else name,
                "drive_id": args.drive_id, "modifiedTime": args.modified}.items() if v}},
            "arquivo": dest.name, "tamanho": dest.stat().st_size, "sha256": bc.sha256_file(dest),
            "licenca": args.licenca}
    if args.fonte:
        item["fonte"] = args.fonte
    item.setdefault("criado_em", bc.now_iso())
    if ext == ".pdf":
        partes, figs, entries = ingest_pdf(item, dest, args.min_fig, args.licenca)
    else:
        partes, figs, entries = ingest_html(item, dest, args.licenca)
    for f in bc.index_item_dir(item_id).glob("contact_sheet*.jpg"):
        f.unlink()
    cs = sheet(item_id, entries)
    item["contact_sheets"] = [cs.name] if cs else []
    bc.save_json(d / "doc_text.json", {"item_id": item_id, "partes": partes})
    bc.save_json(d / "figuras.json", figs)
    bc.save_json(bc.index_item_dir(item_id) / "figuras.json", figs)
    item["n_figuras"] = len(figs)
    if (bc.index_item_dir(item_id) / "segments.json").exists() and prev.get("status") == "indexado":
        item["status"] = "indexado"  # reabastecimento de work/: o item continua indexado
    else:
        item["status"] = "aguardando-segmentos"
    out = write_doc_input(item, partes, figs)
    bc.save_item(item)
    st.done("doc_ingest", signature, [out])
    bc.log(f"{item_id}: OK {item['subtipo']} — {len(partes)} partes, {len(figs)} figuras/capturas, licença {args.licenca}")
    if args.licenca != "propria":
        bc.warn(f"material de {args.licenca}: só usar figuras com crédito/fonte na tela e aviso ao usuário")
    print(f"ITEM_ID={item_id}")
    print(f"LER={out}")
    print(f"ESCREVER={d / 'segments.json'}")


if __name__ == "__main__":
    main()
