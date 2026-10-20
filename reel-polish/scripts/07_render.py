#!/usr/bin/env python3
"""07 — Render final: vídeo polido + áudio polido -> polished.mp4.

Aqui entra a nitidez final (unsharp moderado, só luma — depois do beauty, para não realçar o que o
retoque suavizou) e o fade-in/out opcional (vídeo e áudio). H.264 CRF 17, 30 fps, AAC 192 kbps,
yuv420p, faststart; duração = duração do original (mesma timeline).

Uso:
  python scripts/07_render.py work/<nome>/polish/polish.json
  python scripts/07_render.py polish.json --video beauty.mkv --audio audio_polished.wav --out polished.mp4
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Render do polished.mp4.")
    ap.add_argument("polish")
    ap.add_argument("--workdir")
    ap.add_argument("--video", help="vídeo polido (padrão: retouch.mkv se retouch.enabled, senão beauty.mkv)")
    ap.add_argument("--audio", help="áudio polido (padrão: audio_polished.wav)")
    ap.add_argument("--out", help="saída (padrão: polished.mp4)")
    ap.add_argument("--crf", type=int, default=17)
    ap.add_argument("--preset", default="fast")
    args = ap.parse_args()

    cfg_path = Path(args.polish).resolve()
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    src = pc.polish_source(cfg, cfg_path)
    retouch_on = bool((cfg.get("retouch") or {}).get("enabled"))
    vid = Path(args.video) if args.video else (P["retouch"] if retouch_on else P["beauty"])
    aud = Path(args.audio) if args.audio else P["audio"]
    out = Path(args.out) if args.out else P["final"]
    for p in (vid, aud):
        if not p.exists():
            pc.die(f"entrada não encontrada: {p}")
    look = pc.load_look(cfg.get("look"), cfg_path.parent)
    dur = pc.probe(src)["duration"] or pc.probe(vid)["duration"]
    sharpen = pc.clamp(float(pc.pick(cfg, look, "sharpen", 0.6)), 0, 1.5)
    fade = cfg.get("fade") or {}
    fi, fo = max(0.0, float(fade.get("in_s", 0) or 0)), max(0.0, float(fade.get("out_s", 0) or 0))

    vf = [f"fps={pc.FPS}"]
    if sharpen > 0.01:
        vf.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={0.8 * sharpen:.3f}:chroma_amount=0")
    if fi > 0:
        vf.append(f"fade=t=in:st=0:d={fi:.3f}")
    if fo > 0:
        vf.append(f"fade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}")
    vf.append("format=yuv420p")
    af = [f"aresample={pc.AUDIO_SR}:async=1:first_pts=0"]
    if fi > 0:
        af.append(f"afade=t=in:st=0:d={fi:.3f}")
    if fo > 0:
        af.append(f"afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}")
    af.append("apad")
    pc.log(f"renderizando {out.name} (nitidez {sharpen}, fade {fi}/{fo} s, {pc.fmt_t(dur)}) ...")
    pc.run(["ffmpeg", "-y", "-i", vid, "-i", aud, "-map", "0:v:0", "-map", "1:a:0",
            "-vf", ",".join(vf), "-af", ",".join(af),
            "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf), "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-r", str(pc.FPS), "-c:a", "aac", "-b:a", "192k", "-ar", str(pc.AUDIO_SR),
            "-movflags", "+faststart", "-t", f"{dur:.6f}", out])
    pc.log(f"OK: {out}")


if __name__ == "__main__":
    main()
