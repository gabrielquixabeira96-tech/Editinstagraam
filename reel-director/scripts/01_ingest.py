#!/usr/bin/env python3
"""01 — Ingestão: bruto (iPhone/Android/câmera) -> ingest.mp4 1080x1920 30 fps CFR H.264 BT.709 + ingest.json.

* ffprobe: rotação (display matrix — o ffmpeg aplica a rotação ao decodificar), HDR HLG/PQ (bt2020) -> tone-map BT.709,
  fps variável -> 30 fps CFR, áudio 48 kHz estéreo.
* Enquadramento: vertical ~9:16 -> cover; horizontal -> contain sobre fundo desfocado do próprio vídeo
  (para reenquadrar seguindo o rosto use a reel-polish, etapa `polish`, que tem rastreio YuNet).
* Detecta fundo verde na borda (chroma possível sem custo).
* Narração substituta (`plan.narration.mode = "fastcloner"`): o áudio do ingest passa a ser a narração montada
  (voz clonada do Gabriel) — o resto do pipeline (transcrição, cortes, legenda) segue igual.

Uso: python scripts/01_ingest.py plan.json      (o runner chama isto)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402


def analyze(src) -> dict:
    d = dc.ffprobe_json(src)
    v = next((s for s in d["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in d["streams"] if s.get("codec_type") == "audio"), None)
    if not v:
        dc.die(f"{src}: sem trilha de vídeo")
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
    w, h = v["width"], v["height"]
    if abs(rot) in (90, 270):
        w, h = h, w
    trc = v.get("color_transfer", "") or ""
    hdr = trc in ("arib-std-b67", "smpte2084") or (v.get("color_primaries") == "bt2020")
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return {"width": w, "height": h, "rotation": rot, "hdr": hdr, "transfer": trc, "fps": round(fps, 3),
            "vfr": (v.get("r_frame_rate") != v.get("avg_frame_rate")), "vcodec": v.get("codec_name"),
            "has_audio": a is not None, "duration": float(d["format"].get("duration", 0))}


def green_border_ratio(src, dur) -> float:
    """Fração de pixels de borda 'verde chroma' em 5 quadros."""
    import subprocess
    import numpy as np
    import cv2
    vals = []
    for k in range(5):
        t = dur * (k + 0.5) / 5
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                            "-vf", "scale=270:480", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
        if len(p.stdout) != 270 * 480 * 3:
            continue
        im = np.frombuffer(p.stdout, np.uint8).reshape(480, 270, 3)
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        g = (hsv[..., 0] > 40) & (hsv[..., 0] < 85) & (hsv[..., 1] > 90) & (hsv[..., 2] > 60)
        border = np.concatenate([g[:20].ravel(), g[-20:].ravel(), g[:, :20].ravel(), g[:, -20:].ravel()])
        vals.append(border.mean())
    return float(sum(vals) / len(vals)) if vals else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    W_.mkdir(parents=True, exist_ok=True)
    src = dc.resolve(plan["source"], plan["_dir"])
    if not src.exists():
        dc.die(f"bruto não encontrado: {src}")
    info = analyze(src)
    dc.log(f"ingest: {src.name} {info['width']}x{info['height']} rot={info['rotation']} hdr={info['hdr']} "
           f"fps={info['fps']} dur={info['duration']:.2f}s")

    vf = []
    if info["hdr"]:
        vf.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                  "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    ar = info["width"] / info["height"]
    fit = "cover" if abs(ar - 9 / 16) / (9 / 16) < 0.12 else "contain"
    if fit == "cover":
        vf.append("scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1")
        chain = ",".join(vf + [f"fps={dc.FPS}", "format=yuv420p"])
        fc = f"[0:v]{chain}[v]"
    else:
        pre = (",".join(vf) + ",") if vf else ""
        fc = (f"[0:v]{pre}fps={dc.FPS},split=2[bgs][fgs];[bgs]scale=1080:1920:force_original_aspect_ratio=increase,"
              f"crop=1080:1920,gblur=sigma=30,eq=brightness=-0.08[bg];[fgs]scale=1080:1920:"
              f"force_original_aspect_ratio=decrease:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
              f"format=yuv420p[v]")

    out = W_ / "ingest.mp4"
    voice = None
    nar = plan.get("narration") or {}
    if nar.get("mode") == "fastcloner":
        voice = dc.resolve(nar.get("file") or (W_ / "narracao" / "narration.wav"), plan["_dir"])
        if not voice.exists():
            dc.die(f"narração Fastcloner não encontrada ({voice}). Gere com: python scripts/tts_fastcloner.py "
                   f"{args.plan}  (pede FASTCLONER_API_KEY no .env)")
    cmd = ["ffmpeg", "-i", src]
    if voice:
        cmd += ["-i", voice]
    cmd += ["-filter_complex", fc, "-map", "[v]"]
    if voice:
        cmd += ["-map", "1:a"]
    elif info["has_audio"]:
        cmd += ["-map", "0:a:0"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "1:a", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
            "-metadata:s:v", "rotate=0", out]
    dc.run(cmd)
    green = green_border_ratio(out, info["duration"])
    res = {"source": str(src), "probe": info, "fit": fit, "tone_mapped": info["hdr"],
           "voice_replaced": str(voice) if voice else None,
           "chroma": {"border_green_ratio": round(green, 3), "detected": green >= 0.5},
           "out": str(out), "out_probe": dc.probe(out)}
    dc.save_json(W_ / "ingest.json", res)
    dc.log(f"OK: {out} (fit={fit}, verde na borda={green:.0%})")


if __name__ == "__main__":
    main()
