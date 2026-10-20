#!/usr/bin/env python3
"""00 — Verifica o ambiente do reel-polish: ffmpeg (+ filtros usados), Python, numpy/scipy/OpenCV,
modelos de rosto e de redução de ruído.

Uso:
  python scripts/00_check_env.py
Saída: relatório no terminal; código 0 = pronto, 1 = falta algo essencial.
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402

REQUIRED_FILTERS = ["scale", "crop", "format", "hqdn3d", "curves", "colorbalance", "colortemperature",
                    "huesaturation", "vibrance", "vignette", "unsharp", "fade", "afade", "highpass",
                    "afftdn", "deesser", "equalizer", "treble", "acompressor", "alimiter", "apad",
                    "aresample"]
OPTIONAL_FILTERS = ["vidstabdetect", "vidstabtransform", "arnndn", "lut3d"]


def main():
    ok = True
    print(f"Python: {sys.version.split()[0]}", end="")
    if sys.version_info < (3, 10):
        print("  ✗ (precisa 3.10+)"); ok = False
    else:
        print("  ✓")
    for mod in ("numpy", "scipy", "cv2"):
        try:
            m = __import__(mod)
            print(f"{mod}: {getattr(m, '__version__', '?')}  ✓")
        except Exception as e:
            print(f"{mod}: ✗ ({e}) — pip install -r requirements.txt"); ok = False

    try:
        ff = pc.ffmpeg_bin()
    except SystemExit:
        return 1
    r = subprocess.run([ff, "-version"], capture_output=True, text=True)
    print(f"ffmpeg: {r.stdout.splitlines()[0] if r.stdout else '?'}  ({ff})")
    if not pc.ffprobe_bin():
        print("ffprobe: ✗ não encontrado"); ok = False
    filters = pc.ffmpeg_filters()
    missing = [f for f in REQUIRED_FILTERS if f not in filters]
    if missing:
        print(f"filtros do ffmpeg ausentes (essenciais): {', '.join(missing)}  ✗"); ok = False
    else:
        print("filtros essenciais do ffmpeg: ✓")
    opt_missing = [f for f in OPTIONAL_FILTERS if f not in filters]
    if opt_missing:
        print(f"filtros opcionais ausentes: {', '.join(opt_missing)} — "
              f"{'sem estabilização (vid.stab); ' if any('vidstab' in f for f in opt_missing) else ''}"
              f"{'arnndn indisponível, afftdn será usado; ' if 'arnndn' in opt_missing else ''}"
              f"{'lut3d indisponível (LUT .cube não será aplicada)' if 'lut3d' in opt_missing else ''}")
    else:
        print("filtros opcionais (vidstab, arnndn, lut3d): ✓")

    try:
        det = pc.FaceDetector()
        print(f"detector de rosto: {det.backend}  ✓" + ("" if det.backend == "yunet" else
              "  (aviso: Haar é menos preciso; confira models/face_detection_yunet_2023mar.onnx)"))
    except SystemExit:
        ok = False
    print(f"modelo rnnoise (arnndn): {'✓' if pc.RNNOISE_MODEL.exists() else 'ausente (usa afftdn)'}")
    looks = sorted((pc.SKILL_ROOT / "looks").glob("*.json"))
    print(f"looks: {', '.join(p.stem for p in looks) if looks else '✗ nenhum'}")
    if not looks:
        ok = False
    print("\nAMBIENTE PRONTO" if ok else "\nAMBIENTE INCOMPLETO — veja acima")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
