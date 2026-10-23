#!/usr/bin/env python3
"""00 — Verifica o ambiente do reel-banco: Python, ffmpeg/ffprobe (+ filtros scdet/astats/silencedetect),
numpy/OpenCV/Pillow, faster-whisper (e se o modelo está no cache), PyMuPDF, rank_bm25, requests,
Playwright+Chromium, modelo YuNet da reel-polish e pastas work/ e index/.

Uso:
  python scripts/00_check_env.py [--quiet]
Saída: relatório no terminal; código 0 = pronto, 1 = falta algo essencial.
Não baixa nada (o modelo whisper é baixado na 1ª transcrição, ~480 MB para 'small').
"""
import argparse
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402


def check_mod(name, essential=True, hint="pip install --break-system-packages -r requirements.txt"):
    try:
        m = importlib.import_module(name)
        print(f"  {name}: {getattr(m, '__version__', 'ok')}  OK")
        return True
    except Exception as e:
        print(f"  {name}: {'FALTA' if essential else 'ausente (opcional)'} ({type(e).__name__}) — {hint}")
        return not essential


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args()
    ok = True
    print(f"Python {sys.version.split()[0]}", "OK" if sys.version_info >= (3, 10) else "FALTA (3.10+)")
    ok &= sys.version_info >= (3, 10)

    print("Bibliotecas:")
    for m in ("numpy", "cv2", "PIL", "requests", "rank_bm25"):
        ok &= check_mod(m)
    ok &= check_mod("pymupdf") if importlib.util.find_spec("pymupdf") else check_mod("fitz")
    check_mod("faster_whisper", essential=False,
              hint="necessário só para transcrever vídeos novos (03_transcribe.py)")
    pw = check_mod("playwright", essential=False, hint="necessário só para HTML em 07_doc_ingest.py")
    if pw and importlib.util.find_spec("playwright"):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch()
                b.close()
            print("  chromium (Playwright): OK")
        except Exception as e:
            print(f"  chromium (Playwright): indisponível ({str(e)[:120]}) — capturas de HTML serão puladas")

    model = os.environ.get("REEL_WHISPER_MODEL", "small")
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    cached = list(hub.glob(f"models--Systran--faster-whisper-{model}"))
    print(f"Modelo whisper '{model}': {'no cache' if cached else 'não está no cache (baixa na 1ª transcrição)'}")

    print("ffmpeg:")
    ff = bc.ffmpeg_bin()
    fp = bc.ffprobe_bin()
    r = bc.run([ff, "-hide_banner", "-filters"], check=False)
    filters = r.stdout or ""
    for f in ("scdet", "astats", "silencedetect", "scale", "tile", "drawtext"):
        present = f" {f} " in filters
        essential = f in ("scdet", "astats", "silencedetect", "scale", "tile")
        print(f"  filtro {f}: {'OK' if present else ('FALTA' if essential else 'ausente (opcional)')}")
        if essential and not present:
            ok = False
    print(f"  ffmpeg={ff}  ffprobe={fp}")

    y = bc.yunet_model()
    print(f"YuNet: {'OK' if y.exists() else 'ausente — rosto não será medido'} ({y})")
    print(f"work/: {bc.work_root()}")
    print(f"index/: {bc.index_root()}")
    cfg = bc.SKILL_ROOT / "config.json"
    if cfg.exists():
        c = bc.load_json(cfg).get("drive", {})
        faltam = [k for k, v in c.items() if not v]
        print(f"config.json: {'OK' if not faltam else 'ids vazios: ' + ', '.join(faltam)}")
    print("\nAMBIENTE PRONTO" if ok else "\nAMBIENTE INCOMPLETO — veja acima")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
