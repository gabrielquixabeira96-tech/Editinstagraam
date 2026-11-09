#!/usr/bin/env python3
"""10 — Relatório final: relatorio.md + qc_safe.jpg (prancha de safe area) a partir do que o pipeline gravou.

Lê plan.json, direction.json, qa_report.json, graphics_manifest.json, timemap.json, mix.json/ingest.json e o
polish/qa_report.json; não refaz nada. qc_safe.jpg: um quadro do final.mp4 no meio de cada gráfico, com as zonas
reservadas do Instagram (topo, base, coluna de ações) e a faixa da legenda do estilo desenhadas por cima.

Uso: python scripts/10_report.py plan.json
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402


def frame(video, t):
    import cv2
    import numpy as np
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    return cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)


def qc_sheet(W_, st, man, out):
    import cv2
    import numpy as np
    video = W_ / "final.mp4"
    safe = st["safe"]
    cb = safe.get("caption_band") or {"y0": 1140, "y1": 1245}
    tiles = []
    for m in man:
        t = (m["start_frame"] + m["nframes"] / 2) / dc.FPS
        im = frame(video, t)
        if im is None:
            continue
        ov = im.copy()
        for r in safe["reserved"]:
            cv2.rectangle(ov, (r["x0"], r["y0"]), (r["x1"], r["y1"]), (40, 40, 200), -1)
        cv2.rectangle(ov, (0, cb["y0"]), (1080, cb["y1"]), (60, 180, 220), -1)
        im = cv2.addWeighted(ov, 0.22, im, 0.78, 0)
        c = safe["center"]
        cv2.rectangle(im, (c["x0"], c["y0"]), (c["x1"], c["y1"]), (90, 200, 90), 2)
        if m.get("bbox"):
            x0, y0, x1, y1 = m["bbox"]
            cv2.rectangle(im, (x0, y0), (x1, y1), (87, 141, 176), 3)
        cv2.putText(im, f"{t:5.1f}s {m['comp']}", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (240, 240, 240), 3)
        tiles.append(cv2.resize(im, (360, 640)))
    if not tiles:
        return
    while len(tiles) % 5:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]
    cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    st = dc.load_style(plan.get("style", "director"))
    qa = dc.load_json(W_ / "qa_report.json")
    man = dc.load_json(W_ / "graphics_manifest.json")["items"]
    pq = dc.load_json(W_ / "polish" / "qa_report.json") if (W_ / "polish" / "qa_report.json").exists() else {}
    ing = dc.load_json(W_ / "ingest.json")
    fin = dc.probe(W_ / "final.mp4")
    segs, _ = dc.timemap(W_)
    qc_sheet(W_, st, man, W_ / "qc_safe.jpg")

    L = [f"# Relatório — {Path(plan['source']).stem}", ""]
    L += ["## Entrega",
          f"- `final.mp4`: {fin.get('width')}×{fin.get('height')}, {fin.get('duration', 0):.1f} s, estilo `{plan.get('style')}`",
          f"- QA reel-director: **{qa['veredito']}** — {qa['checagens']}",
          f"- QA reel-polish: {pq.get('status', '—')}", ""]
    if qa.get("defeitos"):
        L += ["Defeitos: " + "; ".join(qa["defeitos"]), ""]
    if qa.get("avisos"):
        L += ["Avisos: " + "; ".join(qa["avisos"]), ""]
    pre = Path(plan["source"]).with_suffix(".precut.json")
    L += ["## Ingestão",
          f"- fonte: `{plan['source']}` ({ing['probe']['width']}×{ing['probe']['height']}, {ing['probe']['duration']:.1f} s, "
          f"enquadramento de ingestão `{ing['fit']}`, fundo verde {ing['chroma']['border_green_ratio']:.0%})"]
    if pre.exists():
        pc = dc.load_json(pre)
        L += [f"- pré-corte do bruto `{pc['bruto']}` (HDR→BT.709: {pc['hdr_tonemapped']}): "
              + ", ".join(f"{r['src_start']:.1f}–{r['src_end']:.1f}s" for r in pc["ranges"])]
    pj = plan.get("polish", {})
    L += ["", "## Beauty / retoque",
          f"- look `{pj.get('look')}`, fit `{pj.get('fit', 'auto')}`, framing `{pj.get('framing')}`, beauty {pj.get('beauty_intensity')}, "
          f"grão {pj.get('grain')}, exposição {pj.get('exposure')}",
          f"- retoque: {pj.get('retouch')} (preset gabriel; barba 0; sem hairline_shape/beard_shape)", ""]
    L += ["## Gancho", f"- **{plan.get('hook', {}).get('title', '')}** — {plan.get('hook', {}).get('why', '')}", ""]
    L += ["## Cortes (tempo do bruto)"] + [f"- {c['src']}: {c['motivo']}" for c in plan.get("cuts", [])] + [""]
    L += ["## Trecho a trecho (nova timeline ← timeline do clipe)"]
    for s in segs:
        L.append(f"- {s['dst_start']:6.2f}–{s['dst_start'] + (s['src_end'] - s['src_start']):6.2f}s ← "
                 f"{s['src_start']:.2f}–{s['src_end']:.2f}s")
    L += ["", "## Elementos gráficos aplicados (nova timeline)"]
    for m, g in zip(man, plan["graphics"]):
        L.append(f"- {m['start_frame'] / dc.FPS:5.2f}s +{m['nframes'] / dc.FPS:.1f}s `{g['comp']}` y={g['props'].get('y')} "
                 f"bbox {m.get('bbox')} — {g.get('why', '')}")
    L += ["", "## Ritmo visual"]
    for z in plan.get("zoom", []):
        L.append(f"- push-in {z.get('scale')} em {dc.map_t(z['t'], segs):.2f}s — {z.get('why', '')}")
    alt = plan.get("aroll_alternate") or {}
    if alt:
        L.append(f"- enquadramento alternado {alt.get('scale')} em {len(alt.get('cuts', []))} jump cuts — {alt.get('why', '')}")
    L.append(f"- maior intervalo sem evento visual: {qa['checagens'].get('max_static_gap_s')} s")
    mx = W_ / "mix.json"
    L += ["", "## Áudio", f"- voz original polida (reel-polish); sem trilha, sem SFX (estilo {plan.get('style')}); "
          f"{qa['checagens'].get('lufs')} LUFS, TP {qa['checagens'].get('true_peak_dbtp')} dBTP"
          + (f"; mix: `{mx.name}`" if mx.exists() else ""),
          "", "## Cenas IA", "- nenhuma (sem custo de IA)",
          "", "## QA", "- prancha de safe area: `qc_safe.jpg` (vermelho = zonas reservadas do Instagram; amarelo = faixa da "
          "legenda; verde = área central; latão = caixa do gráfico)",
          "", "## Pendências", "- revisar a legenda do post em `post.txt` (rascunho gerado do gancho)"]
    (W_ / "relatorio.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    dc.log(f"OK: {W_ / 'relatorio.md'} e {W_ / 'qc_safe.jpg'}")


if __name__ == "__main__":
    main()
