#!/usr/bin/env python3
"""09 — Prévia estática do visual (antes de aprovar o plano): quadro do A-roll + gráfico(s) + legenda no estilo.

Serve para o Gabriel julgar a identidade visual (estilo do plano: cards, tipografia, cor, legenda) num único JPG,
sem rodar cut/compose/render. Não altera nada do pipeline.

* Quadro: polish/polished.mp4 (ou --video) no tempo t da timeline ORIGINAL (a do transcript.json/plan).
* Gráficos: os itens de plan.graphics ativos em t (ou --graphics i,j) renderizados pelo Remotion (render.mjs) com o
  `look` do estilo; usa o quadro do gráfico correspondente a t (mín. 18 quadros após a entrada, animação completa).
* Legenda: a linha de palavras do transcript.json em volta de t, no estilo de legenda do plano (mesmos campos que a
  reel-editor usa: font, font_file, font_size, text/highlight/outline/shadow, position_y), palavra atual destacada.

Uso: python scripts/09_preview.py plan.json --t 18.2 [--t 41.5 ...] --out work/<n>/preview_grand_hotel.jpg
     (várias --t -> prancha lado a lado, cada painel com 1080 px de largura reduzido por --panel-width)
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402


def ass_color(hexstr, alpha=0.0):
    h = hexstr.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{int(round(alpha * 255)):02X}{b}{g}{r}".upper()


def subtitle_line(words, t, wmax):
    """Linha de até wmax palavras contendo t (palavra atual = a que contém t ou a última antes)."""
    idx = max([i for i, w in enumerate(words) if w["start"] <= t] or [0])
    a = (idx // wmax) * wmax
    # recomeça a linha em pausa > 0,45 s (como a reel-editor)
    for j in range(idx, a, -1):
        if words[j]["start"] - words[j - 1]["end"] > 0.45:
            a = j
            break
    line = words[a:a + wmax]
    return line, idx - a


def make_ass(path, line, cur, st):
    W, H = dc.W, dc.H
    sm = st["safe_margin"]
    y = float(st["position_y"]) * H
    x = sm["left"] + (W - sm["left"] - sm["right"]) / 2.0
    prim, hi = ass_color(st["text_color"]), ass_color(st["highlight_color"])
    outline, back = ass_color(st["outline_color"]), ass_color(st.get("shadow_color", "#000000"), 0.4)
    bold = -1 if st.get("bold") else 0
    toks = []
    for k, w in enumerate(line):
        txt = w["word"].strip()
        if st.get("strip_punctuation", True):
            txt = txt.strip(".,;:!?…")
        if st.get("uppercase"):
            txt = txt.upper()
        toks.append(f"{{\\c{hi}}}{txt}{{\\r}}" if k == cur else txt)
    Path(path).write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Reel,{st['font']},{st['font_size']},{prim},{prim},{outline},{back},{bold},0,0,0,100,100,0,0,1,{st['outline_px']},{st.get('shadow_px', 0)},5,{sm['left']},{sm['right']},{sm['bottom']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,Reel,,0,0,0,,{{\\an5\\pos({x:.0f},{y:.0f})}}{" ".join(toks)}
""", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--t", type=float, action="append", required=True)
    ap.add_argument("--graphics", help="índices de plan.graphics (padrão: os ativos em t)")
    ap.add_argument("--video", help="vídeo do A-roll (padrão: polish/polished.mp4, senão ingest.mp4)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--panel-width", type=int, default=540)
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    st = dc.load_style(plan.get("style", "director"))
    sub_p = dc.SKILL / "styles" / st["subtitle_style"] if st.get("subtitle_style") else dc.EDITOR / "styles" / "default.json"
    sst = dc.load_json(sub_p)
    font_dir = dc.resolve(sst.get("font_file", ""), sub_p.parent).parent if sst.get("font_file") else dc.EDITOR / "fonts"
    words = dc.load_json(W_ / "transcript.json")["words"]
    video = Path(args.video) if args.video else (W_ / "polish" / "polished.mp4")
    if not video.exists():
        video = W_ / "ingest.mp4"
    tmp = Path(tempfile.mkdtemp(prefix="preview_", dir=str(W_)))
    panels = []
    for n, t in enumerate(args.t):
        frame = tmp / f"f{n}.png"
        dc.run(["ffmpeg", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-vf", "scale=1080:1920", frame])
        if args.graphics:
            sel = [int(i) for i in args.graphics.split(",")]
        else:
            sel = [i for i, g in enumerate(plan["graphics"]) if g["t"] <= t < g["t"] + g["dur"]]
        jobs, picks = [], []
        for i in sel:
            g = plan["graphics"][i]
            props = dict(g.get("props", {}))
            nfr = max(24, dc.nframes(float(g["dur"])))
            props["durationInFrames"] = nfr
            if st.get("look") and "look" not in props:
                props["look"] = st["look"]
            k = min(nfr - 12, max(18, dc.nframes(t - float(g["t"]))))
            out = tmp / f"g{n}_{i}"
            jobs.append({"id": g["comp"], "props": props, "outDir": str(out)})
            picks.append(out / f"frame-{k:02d}.png" if nfr <= 100 else out / f"frame-{k:03d}.png")
        if jobs:
            dc.save_json(tmp / f"jobs{n}.json", {"concurrency": 1, "jobs": jobs})
            r = subprocess.run(["node", str(dc.REMOTION / "render.mjs"), str(tmp / f"jobs{n}.json")],
                               capture_output=True, text=True)
            if r.returncode != 0:
                dc.die(f"render.mjs falhou:\n{(r.stdout + r.stderr)[-2000:]}")
        picks = [p if p.exists() else sorted(p.parent.glob("frame-*.png"))[min(len(list(p.parent.glob('frame-*.png'))) - 1, 20)]
                 for p in picks]
        line, cur = subtitle_line(words, t, int(sst.get("words_per_line", {}).get("max", 4)))
        ass = tmp / f"s{n}.ass"
        make_ass(ass, line, cur, sst)
        inputs, fc, last = ["-i", frame], "", "[0:v]"
        for j, p in enumerate(picks):
            inputs += ["-i", p]
            fc += f"{last}[{j + 1}:v]overlay=0:0[o{j}];"
            last = f"[o{j}]"
        fc += f"{last}ass=filename={ass}:fontsdir={font_dir}[v]"
        pan = tmp / f"p{n}.png"
        dc.run(["ffmpeg", *inputs, "-filter_complex", fc, "-map", "[v]", "-frames:v", "1", pan])
        panels.append(pan)
        dc.log(f"painel t={t:.2f}s: gráficos {sel}, legenda: {' '.join(w['word'].strip() for w in line)}")
    out = Path(args.out)
    if len(panels) == 1:
        dc.run(["ffmpeg", "-i", panels[0], "-q:v", "2", out])
    else:
        ins = sum([["-i", p] for p in panels], [])
        fc = "".join(f"[{i}:v]scale={args.panel_width}:-2[s{i}];" for i in range(len(panels)))
        fc += "".join(f"[s{i}]" for i in range(len(panels))) + f"hstack=inputs={len(panels)}[v]"
        dc.run(["ffmpeg", *ins, "-filter_complex", fc, "-map", "[v]", "-frames:v", "1", "-q:v", "2", out])
    for p in tmp.glob("**/*"):
        if p.is_file():
            p.unlink()
    for d in sorted(tmp.glob("**/"), reverse=True):
        d.rmdir()
    dc.log(f"OK: {out}")


if __name__ == "__main__":
    main()
