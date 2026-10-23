#!/usr/bin/env python3
"""04 — Gráficos animados: plan.graphics -> jobs.json -> Remotion (render.mjs) -> sequências PNG RGBA.

Para cada gráfico do plano gera uma composição Remotion (tempos convertidos para a NOVA timeline pelo timemap)
em work/graphics/<i>_<comp>/frame-XX.png. Escreve graphics_manifest.json com, por gráfico: start (quadro na nova
timeline), nframes, comp, e a caixa alfa (x0,y0,x1,y1) medida nos PNGs — o 05_compose usa start/nframes e o 07_qa
usa a caixa para reprovar texto fora da área segura ou sobre a faixa da legenda.

Cena IA (`shots[].kind=="ai"`): o rótulo "Recriação ilustrativa (IA)" é injetado aqui como um gráfico Label extra
(garante o rótulo mesmo que o Claude esqueça). Idem disclaimer, se faltar, NÃO é injetado (é decisão do Claude).

Uso: python scripts/04_graphics.py plan.json [--concurrency 2]
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402


def png_bbox(d: Path):
    """Caixa alfa máxima (união) nas PNGs da pasta."""
    import cv2
    import numpy as np
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    frames = sorted(d.glob("frame-*.png"))
    for p in frames[:: max(1, len(frames) // 12)]:  # amostra ~12 quadros
        im = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
        if im is None or im.ndim < 3 or im.shape[2] < 4:
            continue
        ys, xs = np.nonzero(im[..., 3] > 8)
        if len(xs):
            x0, y0 = min(x0, int(xs.min())), min(y0, int(ys.min()))
            x1, y1 = max(x1, int(xs.max())), max(y1, int(ys.max()))
    if x1 < 0:
        return None
    return [x0, y0, x1 + 1, y1 + 1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--concurrency", type=int, default=None)
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    st = dc.load_style(plan.get("style", "director"))
    segs, _ = dc.timemap(W_)
    total = dc.rc.timemap_total(segs)
    gdir = W_ / "graphics"
    gdir.mkdir(parents=True, exist_ok=True)

    items = list(plan["graphics"])
    # rótulo IA automático por cena IA sem Label cobrindo o mesmo intervalo
    for s in plan["shots"]:
        if s.get("kind") == "ai":
            items.append({"t": s["t"], "dur": s.get("dur", 3), "comp": "Label",
                          "props": {"text": st["reach"]["ai_label_text"], "y": 1180, "warn": True},
                          "why": "rótulo obrigatório de cena IA", "_auto": True})

    jobs, manifest = [], []
    for i, g in enumerate(items):
        t0, t1 = float(g["t"]), float(g["t"]) + float(g["dur"])
        ns = dc.map_t(t0, segs)
        ne = dc.map_t(min(t1, (segs[-1]["src_end"] if segs else t1)), segs)
        nfr = max(1, dc.nframes(min(ne, total) - ns))
        comp = g["comp"]
        props = dict(g.get("props", {}))
        props["durationInFrames"] = nfr
        if st.get("look") and "look" not in props:   # estilo visual (ex.: "classico") → componentes *Classico
            props["look"] = st["look"]
        # y padrão por tipo, se o Claude não informou
        slots = st["slots"]
        default_y = {"HookTitle": slots["hook"]["y"], "Counter": slots["card"]["y"],
                     "BarChart": slots["chart"]["y"], "LineChart": slots["chart"]["y"],
                     "ArticleFigure": slots["figure"]["y"], "LowerThird": slots["lower_third"]["y"],
                     "ProgressBar": slots["progress"]["y"], "BigText": slots["bigtext"]["y"]}
        if comp in default_y and "y" not in props:
            props["y"] = default_y[comp]
        out = gdir / f"{i:02d}_{comp}"
        jobs.append({"id": comp, "props": props, "outDir": str(out)})
        manifest.append({"i": i, "comp": comp, "start_frame": dc.nframes(ns), "nframes": nfr,
                         "dir": str(out), "auto": g.get("_auto", False), "why": g.get("why", ""),
                         "behind": bool(g.get("behind", False)), "opacity": float(g.get("opacity", 1.0))})

    conc = args.concurrency or st["render"].get("remotion_concurrency", 2)
    spec = {"concurrency": conc, "jobs": jobs}
    dc.save_json(W_ / "jobs.json", spec)
    if jobs:
        dc.log(f"renderizando {len(jobs)} gráfico(s) no Remotion (bundle único)...")
        r = subprocess.run(["node", str(dc.REMOTION / "render.mjs"), str(W_ / "jobs.json")],
                           capture_output=True, text=True)
        out = (r.stdout or "") + (r.stderr or "")
        for ln in out.splitlines():
            if ln.startswith("✓") or ln.startswith("bundle"):
                dc.log("  " + ln)
        if r.returncode != 0:
            dc.die(f"render.mjs falhou:\n{out[-2000:]}")

    for m in manifest:
        m["bbox"] = png_bbox(Path(m["dir"]))
    dc.save_json(W_ / "graphics_manifest.json", {"total_frames": dc.nframes(total), "items": manifest})
    dc.log(f"OK: {len(manifest)} gráfico(s) -> {W_/'graphics_manifest.json'}")


if __name__ == "__main__":
    main()
