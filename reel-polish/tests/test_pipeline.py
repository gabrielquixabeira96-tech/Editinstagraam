#!/usr/bin/env python3
"""Smoke-test do reel-polish com um vídeo sintético curto (gerado aqui, sem dependências externas).

Gera um "talking head" de desenho (rosto que o YuNet detecta) que se move devagar num quadro
horizontal 1280×720 com ruído e tom azulado, mais um áudio de tom + ruído; roda o pipeline inteiro
em mode raw e depois um mode final sobre o resultado, e exige QA "aprovado" e timeline intacta.

Uso:  python tests/test_pipeline.py [--keep]
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import polish_common as pc  # noqa: E402

W, H, FPS, DUR = 1280, 720, 30, 4.0


def draw_face(img, cx, cy, s):
    import cv2
    cv2.ellipse(img, (cx, cy), (int(0.42 * s), int(0.55 * s)), 0, 0, 360, (120, 160, 210), -1)
    cv2.ellipse(img, (cx, cy - int(0.45 * s)), (int(0.45 * s), int(0.25 * s)), 0, 180, 360, (30, 25, 20), -1)
    for dx in (-0.17, 0.17):
        ex, ey = cx + int(dx * s), cy - int(0.12 * s)
        cv2.ellipse(img, (ex, ey), (int(0.09 * s), int(0.05 * s)), 0, 0, 360, (245, 245, 245), -1)
        cv2.circle(img, (ex, ey), int(0.035 * s), (40, 60, 80), -1)
        cv2.circle(img, (ex, ey), int(0.015 * s), (0, 0, 0), -1)
        cv2.ellipse(img, (ex, ey - int(0.1 * s)), (int(0.1 * s), int(0.02 * s)), 0, 0, 360, (30, 25, 20), -1)
    cv2.ellipse(img, (cx, cy + int(0.08 * s)), (int(0.03 * s), int(0.08 * s)), 0, 0, 360, (100, 140, 190), -1)
    cv2.ellipse(img, (cx, cy + int(0.28 * s)), (int(0.14 * s), int(0.045 * s)), 0, 0, 360, (90, 80, 180), -1)


def make_sample(path):
    import cv2
    rng = np.random.default_rng(7)
    wr = pc.FrameWriter(path.with_suffix(".v.mkv"), W, H, fps=FPS, crf=14)
    n = int(DUR * FPS)
    for i in range(n):
        img = np.full((H, W, 3), (170, 150, 135), np.uint8)              # fundo azulado (frio)
        cv2.rectangle(img, (0, int(H * 0.75)), (W, H), (90, 80, 70), -1)  # "mesa"
        cx = int(W * 0.40 + 60 * math.sin(i / n * 2 * math.pi))
        cy = int(H * 0.45 + 15 * math.cos(i / n * 2 * math.pi))
        draw_face(img, cx, cy, 240)
        img = np.clip(img.astype(np.int16) + rng.normal(0, 4, img.shape).astype(np.int16), 0, 255).astype(np.uint8)
        wr.write(img)
    wr.close()
    # áudio: tom modulado + ruído branco baixo
    sr = 48000
    t = np.arange(int(DUR * sr)) / sr
    voice = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 2.5 * t)))
    noise = 0.02 * rng.standard_normal(len(t))
    a = (voice + noise).astype(np.float32)
    raw = path.with_suffix(".a.f32")
    raw.write_bytes(np.stack([a, a], 1).tobytes())
    pc.run(["ffmpeg", "-y", "-i", path.with_suffix(".v.mkv"), "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", path])
    path.with_suffix(".v.mkv").unlink()
    raw.unlink()


def run(cmd):
    r = subprocess.run([sys.executable] + [str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True)
    if r.returncode not in (0, 2):
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit(f"falhou: {' '.join(str(c) for c in cmd)}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="não apaga a pasta temporária")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="reel_polish_test_"))
    try:
        src = tmp / "sample.mp4"
        print("gerando vídeo sintético ...")
        make_sample(src)
        wd = pc.default_workdir(src)
        wd.mkdir(parents=True)
        cfg = {"source": "../../../sample.mp4", "mode": "raw", "framing": "medium", "fit": "auto",
               "stabilize": False, "look": "looks/cinematic.json", "beauty_intensity": 40, "sharpen": 0.8,
               "audio": {"denoise_db": 12}, "fade": {"in_s": 0.2, "out_s": 0.2}}
        pc.save_json(wd / "polish.json", cfg)
        print("mode raw: run_all ...")
        r = run([ROOT / "scripts" / "run_all.py", wd / "polish.json"])
        qa = pc.load_json(wd / "qa_report.json")
        print(json.dumps({k: v.get("ok") for k, v in qa["checagens"].items()}, ensure_ascii=False))
        assert qa["status"] == "aprovado", f"QA raw reprovado: {qa['defeitos']}"
        fi = pc.probe(wd / "polished.mp4")
        assert (fi["width"], fi["height"]) == (1080, 1920)
        assert abs(fi["duration"] - DUR) <= 1.5 / FPS, fi["duration"]
        assert qa["checagens"]["enquadramento"]["na_zona_alvo_frac"] >= 0.95
        assert (wd / "compare_sheet.png").exists() and (wd / "analysis_sheet.png").exists()
        # cache: segunda execução não refaz nada
        r2 = run([ROOT / "scripts" / "run_all.py", wd / "polish.json"])
        assert "▶" not in r2.stdout, "cache falhou: etapas refeitas sem mudança"
        # mudança só de beauty -> refaz beauty, render, qa (não reframe/grade)
        cfg["beauty_intensity"] = 20
        pc.save_json(wd / "polish.json", cfg)
        r3 = run([ROOT / "scripts" / "run_all.py", wd / "polish.json"])
        assert "▶ beauty" in r3.stdout and "▶ grade" not in r3.stdout and "▶ reframe" not in r3.stdout, r3.stdout
        # mode final sobre o polished.mp4
        wd2 = tmp / "work" / "final_mode"
        wd2.mkdir(parents=True)
        pc.save_json(wd2 / "polish.json", {"source": str(wd / "polished.mp4"), "mode": "final",
                                           "look": "looks/clean.json", "beauty_intensity": 25})
        print("mode final: run_all ...")
        run([ROOT / "scripts" / "run_all.py", wd2 / "polish.json"])
        qa2 = pc.load_json(wd2 / "qa_report.json")
        assert qa2["status"] == "aprovado", f"QA final reprovado: {qa2['defeitos']}"
        # retoque avançado (05b): só se o mediapipe estiver instalado; exige a mesma contagem de quadros e áudio
        try:
            import mediapipe  # noqa: F401
            has_mp = True
        except Exception:
            has_mp = False
        if has_mp:
            print("retouch (05b): trecho curto ...")
            cfg3 = pc.load_json(wd2 / "polish.json")
            cfg3["retouch"] = {"enabled": True, "nose_tip_reduce": 0.15, "hairline": 0.6, "beard": 0.6,
                               "skin_texture": 0.5, "protect_bottom_from": 0.8}
            pc.save_json(wd2 / "polish.json", cfg3)
            out3 = wd2 / "retouch_test.mp4"
            run([ROOT / "scripts" / "05b_retouch.py", wd2 / "polish.json", "--in", wd / "polished.mp4",
                 "--clip", "0.5", "1.0", "--out", out3])
            assert out3.exists(), "05b não gerou saída"
            assert pc.count_frames(out3) == 30, pc.count_frames(out3)
            assert pc.probe(out3)["has_audio"], "05b perdeu o áudio"
            # etapa integrada no run_all: retouch entra entre beauty e render
            r4 = run([ROOT / "scripts" / "run_all.py", wd2 / "polish.json", "--skip-qa"])
            assert "▶ retouch" in r4.stdout and "▶ render" in r4.stdout and "▶ grade" not in r4.stdout, r4.stdout
            assert (wd2 / "retouch.mkv").exists()
            assert pc.count_frames(wd2 / "polished.mp4") == pc.count_frames(wd / "polished.mp4")
        else:
            print("retouch (05b): mediapipe ausente — pulado")
        print("OK — todos os testes passaram")
    finally:
        if args.keep:
            print(f"pasta mantida: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
