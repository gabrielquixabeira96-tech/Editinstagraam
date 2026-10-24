#!/usr/bin/env python3
"""07 — Ingestão de documento (PDF ou HTML/artefato) já baixado pelo Claude (conector Drive ou Artifact read).

PDF (PyMuPDF):
  * texto por página -> doc_text.json {"partes": [{"ref": {"pagina": n}, "titulo", "texto"}]}
  * figuras: imagens embutidas >= --min-fig px (lado menor) extraídas para work/banco/<id>/figuras/pN_fK.png,
    com página, bbox e LEGENDA PRÓXIMA (bloco de texto logo abaixo/acima que começa com Fig/Figura/Figure/
    Tabela/Table/Quadro, senão o bloco mais próximo); render da página inteira (pN.jpg, 110 dpi) para contexto.
HTML (Playwright + Chromium, viewport 540x960, deviceScaleFactor 2, tema claro por padrão):
  * seções: se a página tem >= 2 <section>/<article> de topo com título (aulas-atlas), 1 parte por seção com o
    texto renderizado inteiro dela (+ "intro" antes da 1ª e "fim"/rodapé depois da última; ref.ancora = id da
    seção); senão, cada h1/h2/h3 com o texto até o próximo título. Texto oculto (glossário recolhido, "caminho
    errado") entra no fim da parte sob "[Oculto até interação]";
  * captura de tela de cada seção (work/banco/<id>/secoes/sNN.png, topo até 1920 px CSS) e da página inteira
    (pagina_inteira.png, só se <= 12000 px); --blocos ".key,.divg" captura blocos destacados (secoes/sNN_bK.png);
  * imagens embutidas (data:/file:, lado menor >= --min-fig) -> figuras/sNN_iK.<ext>, com título, legenda
    próxima (figcaption/.cap/.caption/.legenda/.credit) e licença lida da legenda (CC BY/CC0/domínio público =
    terceiros + licenca_detalhe + fonte; fonte bibliográfica sem licença = desconhecida); --clicar ".steps button"
    clica abas/etapas uma a uma e extrai as imagens e o texto que só aparecem depois do clique;
  * --offline bloqueia requisições externas (Google Fonts/CDN): fontes da web caem no fallback local do CSS.
    As opções usadas ficam em item.json -> extracao e são reaproveitadas no reabastecimento (sem repetir flags).
    Se o Chromium falhar, extrai só o texto (aviso).

Campo obrigatório --licenca propria|terceiros|desconhecida (vai para item.json e para cada figura). Figuras de
terceiros só podem ir para um Reel com a licença registrada e aviso de fonte.

Saídas: item.json, doc_text.json, figuras.json, doc_input.md (para o Claude escrever segments.json com
t0/t1 nulos e `ref`), miniaturas 320 px em index/items/<id>/thumbs/ e contact_sheet.jpg (figuras/seções).

Uso:
  python scripts/07_doc_ingest.py --local artigo.pdf --licenca terceiros [--fonte "DOI 10.xxxx"] [--drive-id ID]
  python scripts/07_doc_ingest.py --local atlas.html --licenca propria [--name "Atlas cricotireoidostomia"]
  python scripts/07_doc_ingest.py --local atlas.html --licenca propria --offline --blocos ".key,.divg" \
      --clicar ".steps button"          # aula-atlas (pranchas com etapas, Guarde isto, Divergência)
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
# Modo "secoes": a página tem >= 2 <section>/<article> de topo com título (padrão das aulas-atlas): 1 parte por
# seção, com o innerText COMPLETO dela (renderizado, depois do JS), mais "intro" (o que vem antes da 1ª seção:
# título, lead, instruções) e "fim" (rodapé, referências, fontes das figuras), se tiverem texto. <nav> fica fora.
# Modo "titulos": sem seções, cada h1/h2/h3 com o texto até o próximo título (comportamento anterior).
# Em ambos, o texto OCULTO (display:none, [hidden], visibility:hidden: glossário recolhido, "caminho errado",
# respostas) entra no fim da parte sob "[Oculto até interação]".
JS_PAGE = r"""
(opts) => {
  const SKIP = new Set(['SCRIPT','STYLE','TEMPLATE','NOSCRIPT','NAV','LINK','META']);
  const isHidden = (e) => { if (e.hidden) return true; const cs = getComputedStyle(e);
    return cs.display === 'none' || cs.visibility === 'hidden'; };
  const absTop = (e) => e.getBoundingClientRect().top + window.scrollY;
  const absBot = (e) => e.getBoundingClientRect().bottom + window.scrollY;
  const norm = (t) => (t || '').replace(/[ \t ]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim();
  const hiddenIn = (roots) => {
    const out = [];
    roots.forEach(root => {
      const cand = isHidden(root) ? [root] : Array.from(root.querySelectorAll('*'));
      cand.forEach(e => {
        if (SKIP.has(e.tagName) || e.closest('script,style,template,noscript,svg')) return;
        if (!isHidden(e)) return;
        let p = e.parentElement, outer = true;
        while (p && p !== root) { if (isHidden(p)) { outer = false; break; } p = p.parentElement; }
        if (!outer) return;
        const t = (e.textContent || '').replace(/\s+/g, ' ').trim();
        if (t) out.push(t);
      });
    });
    return out;
  };
  const visText = (els) => els.filter(e => !isHidden(e)).map(e => e.innerText || '').join('\n');
  const altura = document.documentElement.scrollHeight;
  const tops = Array.from(document.querySelectorAll('section,article')).filter(s =>
      s.querySelector('h1,h2,h3') && !(s.parentElement && s.parentElement.closest('section,article')));
  const parts = [];
  let modo = 'titulos';
  if (tops.length >= 2) {
    modo = 'secoes';
    const before = [], after = [], between = new Map();
    const walk = (node) => {
      for (const c of node.children) {
        if (SKIP.has(c.tagName) || tops.includes(c)) continue;
        if (tops.some(s => c.contains(s)) || c.querySelector('nav')) { walk(c); continue; }
        if (tops[0].compareDocumentPosition(c) & Node.DOCUMENT_POSITION_PRECEDING) before.push(c);
        else if (tops[tops.length - 1].compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) after.push(c);
        else {
          let k = 0; tops.forEach((s, j) => { if (s.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) k = j; });
          if (!between.has(k)) between.set(k, []); between.get(k).push(c);
        }
      }
    };
    walk(document.body);
    const headOf = (els, dflt) => { for (const e of els) { const h = e.matches('h1,h2,h3') ? e : e.querySelector('h1,h2,h3');
      if (h && h.innerText.trim()) return h.innerText.trim(); } return dflt; };
    const introTxt = norm(visText(before));
    if (introTxt) parts.push({tipo: 'intro', titulo: headOf(before, document.title || 'Início'), texto: introTxt,
      oculto: hiddenIn(before), top: 0, bottom: absTop(tops[0]), ancora: null, nivel: 'INTRO', el: null});
    tops.forEach((s, j) => {
      const extra = between.get(j) || [];
      const h = s.querySelector('h1,h2,h3');
      parts.push({tipo: 'secao', titulo: (h ? h.innerText : '').trim().slice(0, 160),
        texto: norm((s.innerText || '') + (extra.length ? '\n' + visText(extra) : '')),
        oculto: hiddenIn([s, ...extra]), top: absTop(s), bottom: absBot(s), ancora: s.id || null,
        nivel: s.tagName, el: s});
    });
    const fimTxt = norm(visText(after));
    if (fimTxt) parts.push({tipo: 'fim', titulo: headOf(after, 'Rodapé'), texto: fimTxt, oculto: hiddenIn(after),
      top: absBot(tops[tops.length - 1]), bottom: altura, ancora: null, nivel: 'FIM', el: null});
  } else {
    let els = Array.from(document.querySelectorAll('h1,h2,h3'));
    els.forEach((h, i) => {
      const next = els[i + 1];
      let n = h.nextElementSibling; const sib = [];
      while (n && !(['H1','H2','H3'].includes(n.tagName))) { sib.push(n); n = n.nextElementSibling; }
      let txt = visText(sib);
      if (!txt.trim() && h.parentElement) txt = (h.parentElement.innerText || '').replace(h.innerText, '');
      parts.push({tipo: 'titulo', titulo: (h.innerText || '').trim().slice(0, 160), texto: norm(txt),
        oculto: hiddenIn(sib), top: absTop(h), bottom: next ? absTop(next) : altura, ancora: h.id || null,
        nivel: h.tagName, el: null});
    });
  }
  parts.forEach((p, k) => { if (p.el) p.el.setAttribute('data-banco-parte', String(k)); delete p.el; });
  const partOf = (e) => {
    const s = e.closest('[data-banco-parte]'); if (s) return +s.dataset.bancoParte;
    const y = absTop(e); let k = -1; parts.forEach((p, j) => { if (p.top <= y + 1) k = j; }); return k;
  };
  const blocos = [];
  if (opts.blocos) {
    document.querySelectorAll(opts.blocos).forEach(e => {
      if (isHidden(e)) return; const r = e.getBoundingClientRect();
      if (r.width < 20 || r.height < 12) return;
      blocos.push({parte: partOf(e), texto: norm(e.innerText), x: r.left + window.scrollX, y: r.top + window.scrollY,
                   w: r.width, h: r.height, classe: e.className || e.tagName.toLowerCase()});
    });
  }
  return {titulo: document.title || '', altura: altura, modo: modo,
          texto_total: document.body ? document.body.innerText : '', partes: parts, blocos: blocos};
}
"""

# imagens visíveis agora (chamado no estado inicial e depois de cada clique de --clicar)
JS_IMGS = r"""
() => {
  const isHidden = (e) => { for (let n = e; n && n.nodeType === 1; n = n.parentElement) { if (n.hidden) return true;
      const cs = getComputedStyle(n); if (cs.display === 'none' || cs.visibility === 'hidden') return true; } return false; };
  const absTop = (e) => e.getBoundingClientRect().top + window.scrollY;
  const CAP = 'figcaption, .cap, .caption, .legenda, .credit, .credito, .fonte';
  const near = (img, sel) => { let n = img;
    for (let k = 0; k < 5 && n; k++) { n = n.parentElement; if (!n) break;
      if (n.matches && n.matches(sel) && n.innerText.trim()) return n.innerText.trim();
      const c = n.querySelector(sel); if (c && c.innerText.trim()) return c.innerText.trim(); }
    return ''; };
  const partOf = (e) => { const s = e.closest('[data-banco-parte]'); return s ? +s.dataset.bancoParte : null; };
  return Array.from(document.images).filter(i => i.naturalWidth && i.naturalHeight && !isHidden(i)).map(i => ({
    src: i.currentSrc || i.src, w: i.naturalWidth, h: i.naturalHeight, alt: (i.alt || '').trim(),
    top: absTop(i), parte: partOf(i), legenda: near(i, CAP).replace(/\s+/g, ' '),
    titulo: near(i, 'h1,h2,h3,h4,figcaption').replace(/\s+/g, ' ').slice(0, 160)}));
}
"""

LIC_RE = re.compile(r"\b(CC[\s-]?BY(?:[\s-](?:NC|SA|ND))*(?:[\s-]\d\.\d)?|CC0(?:[\s-]1\.0)?|dom[ií]nio p[uú]blico|"
                    r"public domain)\b", re.I)
FONTE_RE = re.compile(r"\b(fonte|source|cr[eé]dito|credit)\s*:\s*", re.I)
CITE_HINT = re.compile(r"\b(doi|pmc\d|pmid|et al\.?|fig\.?\s*\d)", re.I)


def fig_license(caption: str, licenca_item: str):
    """licença de uma imagem a partir da legenda: CC/domínio público -> terceiros (+detalhe); legenda com fonte
    bibliográfica mas sem licença -> desconhecida; nada disso -> a licença do documento."""
    cap = caption or ""
    m = FONTE_RE.search(cap)
    fonte = cap[m.end():].strip() if m else ""
    lm = LIC_RE.search(cap)
    if lm:
        g = lm.group(1)
        if re.match(r"cc0", g, re.I):
            det = "CC0"
        elif re.match(r"dom|public", g, re.I):
            det = "domínio público"
        else:
            rest = re.sub(r"^CC[\s-]?BY", "", g, flags=re.I).upper()
            ver = re.search(r"\d\.\d", rest)
            det = "CC BY" + "".join("-" + m for m in re.findall(r"NC|SA|ND", rest)) + (f" {ver.group(0)}" if ver else "")
        return "terceiros", det, (fonte or cap)
    if fonte or CITE_HINT.search(cap):
        return "desconhecida", None, (fonte or cap)
    return licenca_item, None, None


def _img_bytes(src_url: str):
    """(bytes, ext) de um data: URI ou file:; None para imagem remota."""
    import base64
    import urllib.parse
    import urllib.request
    if src_url.startswith("data:"):
        head, _, data = src_url.partition(",")
        mime = head[5:].split(";")[0].lower()
        ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
               "image/gif": ".gif", "image/svg+xml": ".svg", "image/avif": ".avif"}.get(mime, ".bin")
        raw = base64.b64decode(data) if ";base64" in head else urllib.parse.unquote_to_bytes(data)
        return raw, ext
    if src_url.startswith("file:"):
        p = Path(urllib.request.url2pathname(urllib.parse.urlparse(src_url).path))
        if p.exists():
            return p.read_bytes(), (p.suffix.lower() or ".bin")
    return None


def ingest_html(item, src: Path, licenca: str, opts: dict, min_fig: int = 120):
    """opts: offline (bool), tema (light|dark|auto), blocos (seletor CSS), clicar (seletor CSS)."""
    import hashlib
    d = bc.item_dir(item["id"])
    sdir, fdir = d / "secoes", d / "figuras"
    for x in (sdir, fdir):
        if x.exists():
            shutil.rmtree(x)
        x.mkdir(parents=True)
    tdir = bc.index_item_dir(item["id"]) / "thumbs"
    if tdir.exists():
        shutil.rmtree(tdir)
    partes, figs, entries, ent_figs, ent_blocos = [], [], [], [], []
    extr = {"offline": bool(opts.get("offline")), "tema": opts.get("tema") or "light",
            "blocos": opts.get("blocos") or "", "clicar": opts.get("clicar") or ""}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch()
            tema = extr["tema"]
            ctx = br.new_context(viewport={"width": 540, "height": 960}, device_scale_factor=2,
                                 color_scheme={"light": "light", "dark": "dark"}.get(tema, "no-preference"))
            pg = ctx.new_page()
            bloqueadas = []
            if extr["offline"]:
                def _route(route):
                    u = route.request.url
                    if u.startswith(("file:", "data:", "blob:", "about:")):
                        route.continue_()
                    else:
                        bloqueadas.append(u)
                        route.abort()
                pg.route("**/*", _route)
            pg.goto(src.resolve().as_uri(), wait_until="load", timeout=60000)
            if tema in ("light", "dark"):
                pg.evaluate("t => document.documentElement.setAttribute('data-theme', t)", tema)
            pg.wait_for_timeout(800)
            info = pg.evaluate(JS_PAGE, {"blocos": extr["blocos"]})
            extr["modo"] = info.get("modo")
            extr["requisicoes_bloqueadas"] = len(bloqueadas)
            if bloqueadas:
                hosts = sorted({re.sub(r"^\w+://([^/]+).*$", r"\1", u) for u in bloqueadas})
                extr["hosts_bloqueados"] = hosts[:20]
                bc.log(f"offline: {len(bloqueadas)} requisição(ões) externa(s) bloqueada(s) ({', '.join(hosts[:6])}) — "
                       "fontes da web caem no fallback local do CSS")
            full_h = int(info.get("altura") or 960)
            if full_h <= 12000:
                pg.screenshot(path=str(sdir / "pagina_inteira.png"), full_page=True)
            else:
                bc.log(f"página com {full_h} px de altura: captura inteira omitida (só por seção)")
            secs = info.get("partes") or []
            if not secs:
                secs = [{"titulo": info.get("titulo") or item["nome"], "texto": info.get("texto_total", ""),
                         "oculto": [], "top": 0, "bottom": full_h, "nivel": "BODY", "ancora": None, "tipo": "pagina"}]
            sid_of = {}
            for i, s in enumerate(secs, 1):
                sid = f"s{i:02d}"
                sid_of[i - 1] = (i, sid)
                texto = s["texto"] or ""
                if s.get("oculto"):
                    texto += "\n\n[Oculto até interação]\n" + "\n".join(f"- {t}" for t in s["oculto"])
                ref = {"secao": i, "secao_id": sid}
                if s.get("ancora"):
                    ref["ancora"] = s["ancora"]
                partes.append({"ref": ref, "titulo": s["titulo"], "texto": texto, "nivel": s.get("nivel"),
                               "tipo": s.get("tipo"), "_top": s["top"], "_bottom": s["bottom"]})
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
                    continue
                f = {"id": sid, "secao": i, "arquivo": f"secoes/{sid}.png", "thumb": f"thumbs/{sid}.jpg",
                     "legenda": s["titulo"], "licenca": licenca, "captura": "540x960@2x", "tipo": "secao",
                     "_y0": top, "_y1": top + h}
                if s.get("ancora"):
                    f["ancora"] = s["ancora"]
                figs.append(f)
                entries.append((f"{sid} {s['titulo'][:24]}", tdir / f"{sid}.jpg"))
            # blocos destacados (--blocos): captura do elemento, para card de citação/B-roll
            nb = {}
            for b in info.get("blocos") or []:
                if b["parte"] is None or b["parte"] < 0 or b["parte"] not in sid_of:
                    continue
                i, sid = sid_of[b["parte"]]
                nb[sid] = nb.get(sid, 0) + 1
                bid = f"{sid}_b{nb[sid]}"
                out = sdir / f"{bid}.png"
                pad = 6
                clip = {"x": max(0, b["x"] - pad), "y": max(0, b["y"] - pad),
                        "width": min(540 - max(0, b["x"] - pad), b["w"] + 2 * pad), "height": min(b["h"] + 2 * pad, 3000)}
                try:
                    pg.screenshot(path=str(out), full_page=True, clip=clip)
                    thumb(out, tdir / f"{bid}.jpg")
                except Exception as e:
                    bc.warn(f"captura do bloco {bid} falhou: {e}")
                    continue
                figs.append({"id": bid, "secao": i, "arquivo": f"secoes/{bid}.png", "thumb": f"thumbs/{bid}.jpg",
                             "legenda": b["texto"][:400], "texto": b["texto"], "classe": b["classe"],
                             "licenca": licenca, "captura": "elemento@2x", "tipo": "bloco"})
                ent_blocos.append((f"{bid} {b['texto'][:22]}", tdir / f"{bid}.jpg"))
            # imagens (estado inicial + cada clique de --clicar), sem repetir a mesma imagem
            seen, ni, remotas = {}, {}, 0
            seen_lines = {k: set((p["texto"] or "").split("\n")) for k, p in enumerate(secs)}
            revealed = {}

            def collect(fase):
                nonlocal remotas
                for im in pg.evaluate(JS_IMGS):
                    h = hashlib.sha1(im["src"].encode("utf-8", "ignore")).hexdigest()
                    if h in seen or min(im["w"], im["h"]) < min_fig:
                        continue
                    k = im["parte"]
                    if k is None:
                        k = max([j for j, s in enumerate(secs) if s["top"] <= im["top"] + 1] or [0])
                    i, sid = sid_of.get(k, (1, "s01"))
                    got = _img_bytes(im["src"])
                    seen[h] = True
                    if not got:
                        remotas += 1
                        continue
                    raw, ext = got
                    ni[sid] = ni.get(sid, 0) + 1
                    fid = f"{sid}_i{ni[sid]}"
                    (fdir / f"{fid}{ext}").write_bytes(raw)
                    lic, det, fonte = fig_license(im["legenda"], licenca)
                    rec = {"id": fid, "secao": i, "arquivo": f"figuras/{fid}{ext}", "thumb": f"thumbs/{fid}.jpg",
                           "largura_px": im["w"], "altura_px": im["h"], "titulo": im["alt"] or im["titulo"],
                           "legenda": im["legenda"][:600], "licenca": lic, "tipo": "imagem", "fase": fase,
                           "_top": im["top"]}
                    if det:
                        rec["licenca_detalhe"] = det
                        if "NC" in det:
                            rec["uso_comercial"] = False
                    if fonte:
                        rec["fonte"] = fonte[:400]
                    try:
                        thumb(fdir / f"{fid}{ext}", tdir / f"{fid}.jpg")
                    except Exception:
                        rec["thumb"] = None
                    figs.append(rec)
                    if rec["thumb"]:
                        ent_figs.append((f"{fid} {lic[:4]}", tdir / f"{fid}.jpg"))

            collect("inicial")
            if extr["clicar"]:
                n = pg.locator(extr["clicar"]).count()
                if n > 400:
                    bc.warn(f"--clicar casa {n} elementos; só os 400 primeiros")
                for j in range(min(n, 400)):
                    loc = pg.locator(extr["clicar"]).nth(j)
                    try:
                        k = loc.evaluate("e => { const s = e.closest('[data-banco-parte]'); "
                                         "e.click(); return s ? +s.dataset.bancoParte : -1; }")
                        pg.wait_for_timeout(120)
                    except Exception as e:
                        bc.warn(f"--clicar #{j}: {str(e)[:120]}")
                        continue
                    collect(f"clique {j + 1}")
                    if k is not None and k >= 0:
                        cur = pg.evaluate("k => { const s = document.querySelector('[data-banco-parte=\"' + k + '\"]'); "
                                          "return s ? s.innerText : ''; }", k)
                        for ln in (cur or "").split("\n"):
                            ln = ln.strip()
                            if ln and ln not in seen_lines[k]:
                                seen_lines[k].add(ln)
                                revealed.setdefault(k, []).append(ln)
                extr["cliques"] = min(n, 400)
            for k, lines in revealed.items():
                partes[k]["texto"] += (f"\n\n[Revelado ao clicar em \"{extr['clicar']}\"]\n" + "\n".join(lines))
            if remotas:
                bc.warn(f"{remotas} imagem(ns) remota(s) não salva(s) (só data:/file: são extraídas)")
            # créditos das figuras de terceiros que aparecem dentro de cada captura de seção (estado inicial)
            for f in figs:
                if f.get("tipo") != "secao":
                    continue
                cred = []
                for g in figs:
                    if (g.get("tipo") == "imagem" and g.get("fase") == "inicial" and g["licenca"] != "propria"
                            and f["_y0"] <= g["_top"] < f["_y1"]):
                        c = g.get("fonte") or g.get("legenda")
                        if c and c not in cred:
                            cred.append(c)
                if cred:
                    f["contem_terceiros"] = cred
            item["titulo_doc"] = info.get("titulo") or None
            br.close()
    except Exception as e:
        bc.warn(f"Playwright/Chromium indisponível ({str(e)[:200]}) — só texto, sem capturas")
        partes, figs, entries, ent_figs, ent_blocos = [], [], [], [], []
        html = src.read_text(encoding="utf-8", errors="ignore")
        chunks = re.split(r"<h[1-3][^>]*>", html, flags=re.I)
        for i, ch in enumerate(chunks, 1):
            title = re.sub(r"<[^>]+>", "", ch.split("</h", 1)[0]).strip()[:160]
            text = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", ch, flags=re.S | re.I))
            partes.append({"ref": {"secao": i, "secao_id": f"s{i:02d}"}, "titulo": title, "texto": text.strip()[:40000]})
        extr["modo"] = "sem-navegador"
    for x in partes:
        x.pop("_top", None)
        x.pop("_bottom", None)
        x["texto"] = re.sub(r"\n{3,}", "\n\n", x["texto"] or "")[:40000]
    for f in figs:
        for k in ("_y0", "_y1", "_top"):
            f.pop(k, None)
    item.update({"subtipo": "html", "secoes": len(partes), "extracao": extr})
    return partes, figs, entries, ent_figs, ent_blocos


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
        L += ["## Figuras / capturas", "",
              "Tipos: `secao` = captura da seção (topo, até 1920 px CSS); `bloco` = captura de um bloco destacado "
              "(--blocos); `imagem` = imagem embutida extraída (licença lida da legenda: CC/domínio público = "
              "`terceiros`; fonte bibliográfica sem licença = `desconhecida`). Captura de seção com `contem_terceiros` "
              "mostra figuras de terceiros: o crédito vai junto.", "",
              "| id | tipo | onde | licença | legenda / texto | miniatura |", "|---|---|---|---|---|---|"]
        for f in figs:
            onde = f"p{f['pagina']}" if "pagina" in f else f"seção {f.get('secao')}"
            lic = f.get("licenca", "") + (f" ({f['licenca_detalhe']})" if f.get("licenca_detalhe") else "")
            if f.get("contem_terceiros"):
                lic += f" + {len(f['contem_terceiros'])} fig. de terceiros"
            leg = (f.get("legenda") or "")
            if f.get("titulo") and f.get("tipo") == "imagem":
                leg = f"{f['titulo']} — {leg}"
            L.append(f"| {f['id']} | {f.get('tipo', 'figura')} | {onde} | {lic} | {leg.replace('|', '/')[:220]} | "
                     f"`{idx / f['thumb']}` |" if f.get("thumb") else f"| {f['id']} | {f.get('tipo', 'figura')} | "
                     f"{onde} | {lic} | {leg.replace('|', '/')[:220]} | — |")
        L.append("")
    L.append("## Texto")
    for p in partes:
        ref = p["ref"]
        lab = f"Página {ref['pagina']}" if "pagina" in ref else (
            f"Seção {ref['secao']} (`{ref['secao_id']}`" + (f", #{ref['ancora']}" if ref.get("ancora") else "")
            + f") — {p.get('titulo', '')}")
        L += ["", f"### {lab}", "", (p["texto"] or "_(sem texto)_")]
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
    ap.add_argument("--min-fig", type=int, default=120,
                    help="lado menor mínimo (px) para extrair figura do PDF ou imagem do HTML")
    ap.add_argument("--offline", action="store_true", default=None,
                    help="HTML: bloqueia toda requisição que não seja file:/data:/blob: (Google Fonts, CDN, "
                         "analytics); fontes da web caem no fallback local do CSS. Use em sessão sem rede")
    ap.add_argument("--online", dest="offline", action="store_false",
                    help="HTML: desfaz um --offline gravado no item.json")
    ap.add_argument("--tema", choices=["light", "dark", "auto"],
                    help="HTML: tema das capturas (padrão light: color-scheme claro + data-theme=\"light\" no <html>)")
    ap.add_argument("--blocos", help="HTML: seletor CSS de blocos a capturar à parte (cards de citação), "
                                     "ex.: \".key,.divg\" nas aulas-atlas (Guarde isto / Divergência)")
    ap.add_argument("--clicar", help="HTML: seletor CSS de abas/etapas a clicar uma a uma para extrair as imagens e o "
                                     "texto que só aparecem depois do clique, ex.: \".steps button\" nas pranchas")
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
    prev = bc.load_item(item_id, required=False) or {}
    # opções de extração HTML: as da linha de comando; na falta, as gravadas no item (reabastecimento determinístico)
    pe = prev.get("extracao") or {}
    opts = {"offline": args.offline if args.offline is not None else bool(pe.get("offline", False)),
            "tema": args.tema or pe.get("tema") or "light",
            "blocos": args.blocos if args.blocos is not None else (pe.get("blocos") or ""),
            "clicar": args.clicar if args.clicar is not None else (pe.get("clicar") or "")}
    sig_parts = [bc.sha256_file(dest), args.licenca, args.fonte, args.min_fig, 2]
    if ext != ".pdf":
        sig_parts += [3, opts["offline"], opts["tema"], opts["blocos"], opts["clicar"]]
    signature = bc.sig(sig_parts)
    if not args.force and st.fresh("doc_ingest", signature, [d / "doc_input.md"]):
        bc.log(f"{item_id}: documento já ingerido (cache)")
        print(f"ITEM_ID={item_id}")
        print(f"LER={d / 'doc_input.md'}")
        return
    item = {**prev, "id": item_id, "nome": Path(name).stem, "tipo": "doc",
            "origem": {**prev.get("origem", {}), **{k: v for k, v in {
                "local": str(src0), "nome_original": src0.name if not args.name else name,
                "drive_id": args.drive_id, "modifiedTime": args.modified}.items() if v}},
            "arquivo": dest.name, "tamanho": dest.stat().st_size, "sha256": bc.sha256_file(dest),
            "licenca": args.licenca}
    if args.fonte:
        item["fonte"] = args.fonte
    item.setdefault("criado_em", bc.now_iso())
    extra_sheets = []
    if ext == ".pdf":
        partes, figs, entries = ingest_pdf(item, dest, args.min_fig, args.licenca)
    else:
        partes, figs, entries, ent_figs, ent_blocos = ingest_html(item, dest, args.licenca, opts, args.min_fig)
        extra_sheets = [x for x in (ent_figs, ent_blocos) if x]
    for f in bc.index_item_dir(item_id).glob("contact_sheet*.jpg"):
        f.unlink()
    sheets = [sheet(item_id, entries)]
    for k, ents in enumerate(extra_sheets, 2):
        sheets.append(sheet(item_id, ents, name=f"contact_sheet_{k:02d}.jpg"))
    item["contact_sheets"] = [s.name for s in sheets if s]
    bc.save_json(d / "doc_text.json", {"item_id": item_id, "partes": partes})
    bc.save_json(d / "figuras.json", figs)
    bc.save_json(bc.index_item_dir(item_id) / "figuras.json", figs)
    item["n_figuras"] = len(figs)
    # resumo de licenças das figuras/capturas (imagens de terceiros exigem crédito na tela)
    fl = {}
    for f in figs:
        key = f.get("licenca", "?") + (f" ({f['licenca_detalhe']})" if f.get("licenca_detalhe") else "")
        fl[key] = fl.get(key, 0) + 1
    item["figuras_licenca"] = {"por_licenca": fl,
                               "imagens_terceiros": sum(1 for f in figs if f.get("tipo") == "imagem"
                                                        and f.get("licenca") == "terceiros"),
                               "sem_uso_comercial": [f["id"] for f in figs if f.get("uso_comercial") is False]}
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
