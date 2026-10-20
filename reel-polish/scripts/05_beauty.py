#!/usr/bin/env python3
"""05 — Retoque de pele "invisível" (OpenCV, quadro a quadro, determinístico).

Como um retocador faria, sem "pele de plástico":
  * máscara de pele = cor (YCrCb, limiares suaves) × prior elíptico do rosto rastreado × proteção
    de bordas (Sobel) − olhos − boca; bordas suaves (blur) para não aparecer "recorte";
  * separação de frequências: o detalhe fino (poros, fios) é separado e devolvido quase inteiro;
    só a camada de média frequência (manchas, vermelhidão, irregularidade de tom) passa por um
    bilateral de baixa intensidade — por isso a textura permanece;
  * extras opcionais e sutis: clareamento sob os olhos, brilho nos olhos (esclera/catchlight),
    redução de brilho oleoso (testa/nariz).
Intensidade 0–100 (`beauty_intensity`, padrão 35). 0 = copia a entrada sem tocar.

Duas passagens: (1) detecção de rosto amostrada a 6 fps no vídeo de entrada (YuNet) + suavização;
(2) processamento quadro a quadro em pipe (ffmpeg rawvideo -> OpenCV -> ffmpeg).

Entrada: graded.mkv (polish.json). Saída: beauty.mkv (só vídeo).

Uso:
  python scripts/05_beauty.py work/<nome>/polish/polish.json
  python scripts/05_beauty.py polish.json --intensity 50 --preview 12.5 --out preview.png
"""
import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402

DET_FPS = 6
DET_WIDTH = 540


# ---------------------------------------------------------------- passo 1: rastrear o rosto
def track_faces(video, n_frames, det):
    """Detecta a 6 fps (540 px) e devolve arrays por quadro (30 fps) já suavizados, em px do vídeo."""
    rd = pc.FrameReader(video, width=DET_WIDTH, fps=DET_FPS)
    sc = rd.src_w / rd.w
    rows, prev = [], None
    for i, fr in enumerate(rd):
        f = pc.pick_face(det.detect(fr), prev)
        if f:
            prev = f
            rows.append([i / DET_FPS, f["x"] * sc, f["y"] * sc, f["w"] * sc, f["h"] * sc,
                         f["eyes"][0][0] * sc, f["eyes"][0][1] * sc, f["eyes"][1][0] * sc, f["eyes"][1][1] * sc,
                         f["mouth"][0][0] * sc, f["mouth"][0][1] * sc, f["mouth"][1][0] * sc, f["mouth"][1][1] * sc, 1.0])
        else:
            rows.append([i / DET_FPS] + [None] * 12 + [0.0])
    if not rows or not any(r[-1] for r in rows):
        return None
    cols = []
    for c in range(1, 13):
        v = pc.fill_gaps([r[c] for r in rows])
        cols.append(pc.smooth(v, int(DET_FPS * 1.0)))
    ts = np.array([r[0] for r in rows])
    t_frames = np.arange(n_frames) / pc.FPS
    out = {k: np.interp(t_frames, ts, cols[j]) for j, k in enumerate(
        ["x", "y", "w", "h", "e0x", "e0y", "e1x", "e1y", "m0x", "m0y", "m1x", "m1y"])}
    out["detected_frac"] = float(np.mean([r[-1] for r in rows]))
    return out


# ---------------------------------------------------------------- passo 2: retoque
MASK_SCALE = 0.25   # máscaras e blurs largos são calculados a 1/4 da resolução (são suaves por natureza)


def ellipse_mask(shape, center, axes, soft):
    """Elipse com borda suave, calculada em baixa resolução e ampliada (blur de sigma grande é caro)."""
    import cv2
    s = MASK_SCALE
    h, w = max(2, int(shape[0] * s)), max(2, int(shape[1] * s))
    m = np.zeros((h, w), dtype=np.float32)
    cv2.ellipse(m, (int(center[0] * s), int(center[1] * s)), (max(1, int(axes[0] * s)), max(1, int(axes[1] * s))),
                0, 0, 360, 1.0, -1)
    if soft > 0:
        m = cv2.GaussianBlur(m, (0, 0), max(0.5, soft * s))
    return cv2.resize(m, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)


def wide_blur(img, sigma):
    """GaussianBlur de sigma grande via redução 1/4 (equivalente visualmente, ~16× mais rápido)."""
    import cv2
    s = MASK_SCALE
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(2, int(w * s)), max(2, int(h * s))), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), max(0.5, sigma * s))
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def retouch(frame, F, I, opts):
    """Retoca um quadro BGR (uint8). F: dict com x,y,w,h,olhos,boca em px. I: intensidade 0–1."""
    import cv2
    H, W = frame.shape[:2]
    fx, fy, fw, fh = F["x"], F["y"], F["w"], F["h"]
    x0, y0 = int(max(0, fx - 0.25 * fw)), int(max(0, fy - 0.40 * fh))
    x1, y1 = int(min(W, fx + 1.25 * fw)), int(min(H, fy + 1.20 * fh))
    if x1 - x0 < 16 or y1 - y0 < 16:
        return frame
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    rh, rw = roi.shape[:2]
    ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
    Y, Cr, Cb = ycc[..., 0], ycc[..., 1], ycc[..., 2]

    # --- máscara de pele
    def soft_band(v, lo, hi, width):
        return np.clip((v - lo) / width, 0, 1) * np.clip((hi - v) / width, 0, 1)
    m_color = soft_band(Cr, 133, 182, 8) * soft_band(Cb, 82, 138, 8) * np.clip((Y - 50) / 30, 0, 1)
    cx, cy = fx + fw / 2 - x0, fy + fh * 0.48 - y0
    prior = ellipse_mask((rh, rw), (cx, cy), (0.66 * fw, 0.80 * fh), 0.10 * fw)
    gray_blur = cv2.GaussianBlur(Y, (0, 0), 1.2)
    gx = cv2.Sobel(gray_blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray_blur, cv2.CV_32F, 0, 1, ksize=3)
    edge = np.clip(np.sqrt(gx * gx + gy * gy) / 60.0, 0, 1)
    edge = cv2.GaussianBlur(edge, (0, 0), 1.5)
    e0 = (F["e0x"] - x0, F["e0y"] - y0)
    e1 = (F["e1x"] - x0, F["e1y"] - y0)
    eye_r = 0.13 * fw
    m_eyes = np.clip(ellipse_mask((rh, rw), e0, (eye_r, 0.6 * eye_r), 0.03 * fw) +
                     ellipse_mask((rh, rw), e1, (eye_r, 0.6 * eye_r), 0.03 * fw), 0, 1)
    mc = ((F["m0x"] + F["m1x"]) / 2 - x0, (F["m0y"] + F["m1y"]) / 2 - y0)
    mw = max(abs(F["m1x"] - F["m0x"]) / 2 + 0.06 * fw, 0.1 * fw)
    m_mouth = ellipse_mask((rh, rw), mc, (mw, 0.09 * fw), 0.03 * fw)
    mask = m_color * prior * (1 - 0.85 * edge) * (1 - m_eyes) * (1 - m_mouth)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = wide_blur(mask, max(2.0, 0.025 * fw))
    mask = np.clip(mask, 0, 1)

    # --- separação de frequências (detalhe fino preservado; média frequência suavizada)
    low = cv2.GaussianBlur(roi, (0, 0), 2.0)
    detail = roi - low
    small = cv2.resize(low, (max(8, rw // 2), max(8, rh // 2)), interpolation=cv2.INTER_AREA)
    sig_c, sig_s = 10 + 32 * I, 3 + 6 * I
    small = cv2.bilateralFilter(small, 7, sig_c, sig_s)
    low_s = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_LINEAR)
    keep = 1.0 - 0.45 * I
    smooth = low_s + detail * keep
    amount = min(1.0, 1.6 * I)
    a = (mask * amount)[..., None]
    out = roi + (smooth - roi) * a

    # --- extras, no espaço YCrCb
    ycc2 = cv2.cvtColor(np.clip(out, 0, 255), cv2.COLOR_BGR2YCrCb)
    Y2 = ycc2[..., 0]
    if opts.get("under_eye", True):
        ue = np.zeros((rh, rw), np.float32)
        for (ex, ey) in (e0, e1):
            ue += ellipse_mask((rh, rw), (ex, ey + 0.10 * fw), (0.12 * fw, 0.055 * fw), 0.03 * fw)
        ue = np.clip(ue, 0, 1) * (1 - m_eyes) * m_color
        Y2 += ue * (14 * I)
        ycc2[..., 1] += ue * (-(ycc2[..., 1] - 128) * 0.15 * I)   # menos roxo
    if opts.get("eye_brighten", True):
        Y2 += m_eyes * np.clip(Y2 - 110, 0, 255) * (0.35 * I)
    if opts.get("shine_reduction", True):
        spec = np.clip((Y2 - 215) / 30.0, 0, 1) * mask
        spec = wide_blur(spec, 4)
        local = wide_blur(Y2, 12)
        Y2 -= spec * (Y2 - local) * (1.2 * I)
    ycc2[..., 0] = np.clip(Y2, 0, 255)
    out = cv2.cvtColor(ycc2, cv2.COLOR_YCrCb2BGR)
    frame = frame.copy()
    frame[y0:y1, x0:x1] = np.clip(out, 0, 255).astype(np.uint8)
    return frame


def face_at(T, i):
    return {k: float(T[k][i]) for k in ("x", "y", "w", "h", "e0x", "e0y", "e1x", "e1y", "m0x", "m0y", "m1x", "m1y")}


def main():
    ap = argparse.ArgumentParser(description="Retoque de pele invisível (OpenCV).")
    ap.add_argument("polish")
    ap.add_argument("--workdir")
    ap.add_argument("--in", dest="inp", help="entrada (padrão: graded.mkv)")
    ap.add_argument("--out", help="saída (padrão: beauty.mkv; com --preview, PNG)")
    ap.add_argument("--intensity", type=float, help="0–100 (sobrescreve polish.json)")
    ap.add_argument("--preview", type=float, help="só 1 quadro (PNG) neste instante")
    ap.add_argument("--crf", type=int, default=12)
    args = ap.parse_args()

    cfg_path = Path(args.polish).resolve()
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    inp = Path(args.inp) if args.inp else P["grade"]
    if args.preview is not None and not inp.exists():
        inp = pc.polish_source(cfg, cfg_path)
    if not inp.exists():
        pc.die(f"entrada não encontrada: {inp}")
    I = pc.clamp(float(args.intensity if args.intensity is not None else cfg["beauty_intensity"]), 0, 100) / 100.0
    opts = cfg.get("beauty") or {}
    out = Path(args.out) if args.out else P["beauty"]
    info = pc.probe(inp)
    n_frames = int(round((info["duration"] or 0) * pc.FPS))

    if I <= 0 and args.preview is None:
        pc.log("beauty_intensity = 0 — copiando sem retoque")
        shutil.copyfile(inp, out)
        pc.save_json(workdir / "beauty.json", {"intensity": 0, "applied": False})
        return
    det = pc.FaceDetector()
    pc.log(f"rastreando rosto em {inp.name} ({det.backend}) ...")
    T = track_faces(inp, max(n_frames, 1), det)
    if T is None:
        pc.log("aviso: nenhum rosto — copiando sem retoque")
        if args.preview is None:
            shutil.copyfile(inp, out)
            pc.save_json(workdir / "beauty.json", {"intensity": I * 100, "applied": False, "reason": "sem rosto"})
        return
    pc.log(f"  rosto em {100 * T['detected_frac']:.0f}% das amostras; intensidade {I * 100:.0f}")

    import cv2
    if args.preview is not None:
        rd = pc.FrameReader(inp, start=args.preview, dur=0.05, fps=None)
        fr = next(iter(rd), None)
        rd.close()
        if fr is None:
            pc.die("instante fora do vídeo")
        i = int(np.clip(round(args.preview * pc.FPS), 0, n_frames - 1))
        res = retouch(fr, face_at(T, i), I, opts)
        outp = Path(args.out) if args.out else workdir / f"beauty_preview_{args.preview:.1f}.png"
        cv2.imwrite(str(outp), np.hstack([fr, res]))
        pc.log(f"OK: {outp} (antes | depois)")
        return

    reader = pc.FrameReader(inp, fps=pc.FPS)
    writer = pc.FrameWriter(out, reader.w, reader.h, crf=args.crf)
    i = 0
    for fr in reader:
        writer.write(retouch(fr, face_at(T, min(i, n_frames - 1)), I, opts))
        i += 1
    writer.close()
    pc.save_json(workdir / "beauty.json", {"intensity": I * 100, "applied": True, "frames": i,
                                            "detected_frac": T["detected_frac"], "options": opts})
    pc.log(f"OK: {out} ({i} quadros)")


if __name__ == "__main__":
    main()
