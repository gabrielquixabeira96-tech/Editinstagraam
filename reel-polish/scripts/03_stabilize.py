#!/usr/bin/env python3
"""03 — Estabilização (vid.stab em duas passagens), sem alterar a timeline.

Parâmetros moderados: remove o tremor de mão (alta frequência) e preserva os movimentos lentos
(inclusive o "pan" suave do reenquadramento). `optzoom=1` calcula um zoom estático mínimo para
nunca mostrar bordas pretas.

Se `stabilize` for false (ou "auto" com tremor baixo em analysis.json), o script apenas copia a
entrada para a saída (sem recodificar).

Uso:
  python scripts/03_stabilize.py work/<nome>/polish/polish.json
  python scripts/03_stabilize.py polish.json --in reframe.mkv --out stabilized.mkv --force
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402

SHAKE_THRESHOLD_PCT = 0.25   # % da largura (jitter RMS) a partir do qual "auto" estabiliza


def should_stabilize(cfg, analysis) -> tuple[bool, str]:
    v = cfg.get("stabilize", "auto")
    if v is True or v is False:
        return bool(v), "polish.json"
    j = ((analysis or {}).get("shake") or {}).get("jitter_pct_width", 0.0)
    return j > SHAKE_THRESHOLD_PCT, f"auto (tremor {j:.2f}% da largura, limiar {SHAKE_THRESHOLD_PCT}%)"


def main():
    ap = argparse.ArgumentParser(description="Estabiliza (vid.stab) o vídeo reenquadrado.")
    ap.add_argument("polish")
    ap.add_argument("--workdir")
    ap.add_argument("--in", dest="inp", help="entrada (padrão: reframe.mkv, ou a origem em mode final)")
    ap.add_argument("--out", help="saída (padrão: stabilized.mkv)")
    ap.add_argument("--force", action="store_true", help="estabiliza mesmo que a decisão seja não")
    ap.add_argument("--shakiness", type=int, default=5, help="vidstabdetect shakiness 1–10 (padrão 5)")
    ap.add_argument("--smoothing", type=int, default=20, help="vidstabtransform smoothing (quadros; padrão 20)")
    ap.add_argument("--crf", type=int, default=12)
    args = ap.parse_args()

    cfg_path = Path(args.polish).resolve()
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    inp = Path(args.inp) if args.inp else (P["reframe"] if cfg["mode"] == "raw" else pc.polish_source(cfg, cfg_path))
    out = Path(args.out) if args.out else P["stabilize"]
    if not inp.exists():
        pc.die(f"entrada não encontrada: {inp}")
    analysis = pc.load_json(P["analysis"]) if P["analysis"].exists() else {}
    do, why = should_stabilize(cfg, analysis)
    if args.force:
        do, why = True, "--force"
    filters = pc.ffmpeg_filters()
    if do and ("vidstabdetect" not in filters or "vidstabtransform" not in filters):
        pc.log("aviso: ffmpeg sem vid.stab — estabilização pulada")
        do = False
    if not do:
        pc.log(f"estabilização: não ({why}) — copiando {inp.name} -> {out.name}")
        if inp.resolve() != out.resolve():
            shutil.copyfile(inp, out)
        pc.save_json(workdir / "stabilize.json", {"stabilized": False, "reason": why})
        return
    pc.log(f"estabilização: sim ({why}) — passagem 1/2 (detecção) ...")
    trf = P["stab_trf"]
    pc.run(["ffmpeg", "-y", "-i", inp, "-vf",
            f"vidstabdetect=shakiness={args.shakiness}:accuracy=15:stepsize=6:result={pc.ff_filter_value(trf)}",
            "-f", "null", "-"])
    pc.log("passagem 2/2 (transformação) ...")
    vf = (f"vidstabtransform=input={pc.ff_filter_value(trf)}:smoothing={args.smoothing}:optzoom=1:"
          f"zoomspeed=0.1:interpol=bicubic:crop=black:relative=1,unsharp=5:5:0.3:3:3:0")
    pc.run(["ffmpeg", "-y", "-i", inp, "-vf", vf, "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", str(args.crf), "-pix_fmt", "yuv420p", "-r", str(pc.FPS), out])
    pc.save_json(workdir / "stabilize.json", {"stabilized": True, "reason": why, "shakiness": args.shakiness,
                                               "smoothing": args.smoothing})
    pc.log(f"OK: {out}")


if __name__ == "__main__":
    main()
