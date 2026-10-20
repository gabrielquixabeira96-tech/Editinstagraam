#!/usr/bin/env python3
"""04 — Correção de cor ("grade") na ordem de um colorista, com ffmpeg:

  denoise (hqdn3d leve) -> exposição/contraste (curva de tons calculada a partir do analysis.json:
  níveis, gama para levar a pele ao alvo, S suave, ombro nas altas luzes) -> balanço de branco
  (colortemperature + tint) -> saturação com proteção da pele (huesaturation sem r/y + vibrance)
  -> look (tintas de sombra/meio-tom/alta luz, looks/*.json) -> LUT .cube opcional -> vinheta sutil.

A nitidez final NÃO é aplicada aqui: fica para o render (07), depois do retoque de pele, para não
realçar o que o beauty suavizou.

Entrada: polish.json (+ analysis.json). Em mode raw a entrada de vídeo é stabilized.mkv; em mode
final é o próprio `source`. Saída: graded.mkv (só vídeo).

Uso:
  python scripts/04_grade.py work/<nome>/polish/polish.json
  python scripts/04_grade.py polish.json --preview 12.5 --out preview.png   # 1 quadro, para ajustar
  python scripts/04_grade.py polish.json --print-filter                       # só mostra a cadeia
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402

DENOISE = {"off": None, "minimo": "hqdn3d=0.6:0.5:1.5:1.5", "leve": "hqdn3d=1.2:0.9:2.5:2.5",
           "medio": "hqdn3d=2.5:2.0:4.0:4.0"}


# ---------------------------------------------------------------- curva de tons
def tone_curve(exp_cfg, look, analysis):
    """Devolve (pontos da curva mestre como string do filtro `curves`, dict com o que foi decidido)."""
    ex = (analysis or {}).get("exposure") or {}
    p1, p99 = ex.get("p1", 0.0), ex.get("p99", 1.0)
    face = ex.get("face_luma")
    info = {}
    xs = np.linspace(0, 1, 33)
    y = xs.copy()
    # 1) níveis: só quando o histograma não usa a faixa toda (contraste "lavado")
    black = max(0.0, p1 - 0.02) if p1 > 0.04 else 0.0
    white = min(1.0, p99 + 0.03) if p99 < 0.92 else 1.0
    if black > 0 or white < 1:
        strength = 0.6
        lev = np.clip((y - black) / max(white - black, 1e-3), 0, 1)
        y = (1 - strength) * y + strength * lev
        info["levels"] = {"black": round(black, 3), "white": round(white, 3), "strength": strength}
    # 2) gama: leva a luma da pele ao alvo (só com auto e com rosto medido)
    gamma = 1.0
    if exp_cfg.get("auto", True) and face:
        f = float(np.interp(face, xs, y))
        target = float(exp_cfg.get("target_face_luma", 0.58))
        if f > 0.02:
            gamma = math.log(target) / math.log(f)          # y = x^gamma
            mg = float(exp_cfg.get("max_gamma", 1.6))
            gamma = pc.clamp(gamma, 1 / mg, mg)
            # meio caminho: corrigimos 70% da diferença (evita "cara de HDR")
            gamma = 1 + 0.7 * (gamma - 1)
    y = np.power(np.clip(y, 0, 1), gamma)
    info["gamma"] = round(gamma, 3)
    # 3) contraste em S (pivô 0,5), suave
    c = float(exp_cfg.get("contrast", look.get("contrast", 0.12)))
    s = y * y * (3 - 2 * y)
    y = (1 - c) * y + c * s
    info["contrast"] = c
    # 4) sombras levantadas (preto "fosco", cinematográfico) — padrão 0
    lift = float(exp_cfg.get("lift_shadows", look.get("lift_shadows", 0.0)))
    if lift > 0:
        y = lift + (1 - lift) * y
    info["lift_shadows"] = lift
    # 5) ombro nas altas luzes (protege de estourar)
    r = float(exp_cfg.get("highlight_rolloff", look.get("highlight_rolloff", 0.03)))
    k = 0.80
    if r > 0:
        u = np.clip((y - k) / (1 - k), 0, 1)
        a = 10 * r
        u2 = np.log1p(a * u) / math.log1p(a)
        y = np.where(y > k, k + (1 - k) * u2, y)
    info["highlight_rolloff"] = r
    y = np.clip(y, 0, 1)
    y[0], y[-1] = min(y[0], y[1]), max(y[-1], y[-2])
    pts = " ".join(f"{x:.4f}/{v:.4f}" for x, v in zip(xs[::2], y[::2]))
    return pts, info


# ---------------------------------------------------------------- cadeia de filtros
def build_chain(cfg, look, analysis, cfg_dir):
    f = []
    info = {}
    # denoise
    dn = cfg.get("denoise", "auto")
    if dn == "auto":
        sigma = ((analysis or {}).get("noise") or {}).get("sigma", 1.0)
        dn = "minimo" if sigma < 1.5 else ("leve" if sigma < 4.0 else "medio")
    if DENOISE.get(dn):
        f.append(DENOISE[dn])
    info["denoise"] = dn
    # exposição
    pts, tinfo = tone_curve(cfg.get("exposure") or {}, look, analysis)
    f.append(f"curves=m='{pts}'")
    info["tone"] = tinfo
    # balanço de branco
    wb = cfg.get("white_balance") or {}
    shift = float(wb.get("temperature_shift", 0) or 0)
    if wb.get("auto", True) and shift == 0:
        shift = float(((analysis or {}).get("white_balance") or {}).get("suggested_temperature_shift", 0) or 0)
    shift += float(look.get("temperature_bias", 0) or 0)
    strength = pc.clamp(float(wb.get("strength", 0.7)), 0, 1)
    if abs(shift) >= 25 and strength > 0:
        T = pc.clamp(6500 - shift, 2000, 15000)
        f.append(f"colortemperature=temperature={T:.0f}:mix={strength:.2f}:pl=0.6")
    tint = pc.clamp(float(wb.get("tint", 0) or 0), -1, 1)
    info["white_balance"] = {"temperature_shift": shift, "strength": strength, "tint": tint}
    # saturação com proteção da pele
    sat = float(pc.pick(cfg, look, "saturation", 1.08))
    vib = float(pc.pick(cfg, look, "vibrance", 0.12))
    if abs(sat - 1) > 0.005:
        colors = "g+c+b+m" if look.get("skin_protect", True) else "a"
        f.append(f"huesaturation=saturation={pc.clamp(sat - 1, -1, 1):.3f}:colors={colors}:lightness=1")
        if look.get("skin_protect", True) and sat > 1:
            # um terço do ganho também em r/y, para a pele não ficar "cinza" em relação ao resto
            f.append(f"huesaturation=saturation={pc.clamp((sat - 1) / 3, -1, 1):.3f}:colors=r+y:lightness=1")
    if abs(vib) > 0.005:
        f.append(f"vibrance=intensity={pc.clamp(vib, -2, 2):.3f}")
    info["saturation"], info["vibrance"] = sat, vib
    # look: tintas por faixa tonal + tint do WB (colorbalance com preservação de luminosidade)
    sh, mid, hi = look.get("shadows_rgb", [0, 0, 0]), look.get("midtones_rgb", [0, 0, 0]), look.get("highlights_rgb", [0, 0, 0])
    mid = [mid[0], mid[1] + 0.3 * tint, mid[2]]
    if any(abs(v) > 0.002 for v in list(sh) + list(mid) + list(hi)):
        f.append("colorbalance=" + ":".join([f"rs={sh[0]:.3f}", f"gs={sh[1]:.3f}", f"bs={sh[2]:.3f}",
                                              f"rm={mid[0]:.3f}", f"gm={mid[1]:.3f}", f"bm={mid[2]:.3f}",
                                              f"rh={hi[0]:.3f}", f"gh={hi[1]:.3f}", f"bh={hi[2]:.3f}", "pl=1"]))
    # LUT opcional
    lut = cfg.get("lut") or look.get("lut")
    chain_pre = ",".join(f)
    graph = None
    if lut:
        lp = pc.resolve_path(lut, cfg_dir)
        if not lp.exists():
            pc.die(f"LUT não encontrada: {lp}")
        mix = pc.clamp(float(look.get("lut_mix", 1.0)), 0, 1)
        lut_f = f"lut3d=file={pc.ff_filter_value(lp)}:interp=tetrahedral"
        if mix >= 0.999:
            f.append(lut_f)
        else:
            graph = (f"[0:v]{chain_pre},format=rgb24,split[a][b];[b]{lut_f}[c];"
                     f"[a][c]blend=all_mode=normal:all_opacity={mix:.3f}")
        info["lut"] = {"file": str(lp), "mix": mix}
    # vinheta
    vig = pc.clamp(float(pc.pick(cfg, look, "vignette", 0.35)), 0, 1)
    if vig > 0.01:
        f.append(f"vignette=angle={0.9 * vig:.3f}:mode=forward")
    info["vignette"] = vig
    f.append("format=yuv420p")
    if graph:
        tail = ",".join(x for x in f if not x.startswith(("hqdn3d", "curves", "colortemperature", "huesaturation",
                                                          "vibrance", "colorbalance", "lut3d")))
        return graph + "," + tail, info, True
    return ",".join(f), info, False


def main():
    ap = argparse.ArgumentParser(description="Correção de cor profissional (ffmpeg).")
    ap.add_argument("polish")
    ap.add_argument("--workdir")
    ap.add_argument("--in", dest="inp", help="entrada (padrão: stabilized.mkv em mode raw; source em mode final)")
    ap.add_argument("--out", help="saída (padrão: graded.mkv; com --preview, PNG)")
    ap.add_argument("--preview", type=float, help="gera só 1 quadro (PNG) neste instante, para ajustar")
    ap.add_argument("--print-filter", action="store_true")
    ap.add_argument("--crf", type=int, default=12)
    args = ap.parse_args()

    cfg_path = Path(args.polish).resolve()
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    look = pc.load_look(cfg.get("look"), cfg_path.parent)
    analysis = pc.load_json(P["analysis"]) if P["analysis"].exists() else {}
    inp = Path(args.inp) if args.inp else (P["stabilize"] if cfg["mode"] == "raw" else pc.polish_source(cfg, cfg_path))
    if args.preview is not None and not inp.exists():
        inp = pc.polish_source(cfg, cfg_path)
    if not inp.exists():
        pc.die(f"entrada não encontrada: {inp}")
    chain, info, is_graph = build_chain(cfg, look, analysis, cfg_path.parent)
    pc.log(f"look: {look.get('name')}  |  decisões: {info}")
    if args.print_filter:
        print(chain)
        return
    if args.preview is not None:
        out = Path(args.out) if args.out else workdir / f"grade_preview_{args.preview:.1f}.png"
        cmd = ["ffmpeg", "-y", "-ss", f"{args.preview:.3f}", "-i", inp]
        cmd += ["-filter_complex", chain] if is_graph else ["-vf", chain]
        cmd += ["-frames:v", "1", out]
        pc.run(cmd)
        pc.log(f"OK: {out}")
        return
    out = Path(args.out) if args.out else P["grade"]
    cmd = ["ffmpeg", "-y", "-i", inp]
    cmd += ["-filter_complex", chain] if is_graph else ["-vf", chain]
    cmd += ["-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(args.crf), "-pix_fmt", "yuv420p",
            "-r", str(pc.FPS), out]
    pc.log(f"aplicando grade em {inp.name} ...")
    pc.run(cmd)
    pc.save_json(workdir / "grade.json", {"input": str(inp), "look": look.get("_path"), "filter": chain, "decisions": info})
    pc.log(f"OK: {out}")


if __name__ == "__main__":
    main()
