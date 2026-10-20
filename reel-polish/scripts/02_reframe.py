#!/usr/bin/env python3
"""02 — Reenquadramento 9:16 dinâmico seguindo o rosto (sem alterar a timeline).

Regras de câmera (como um operador de steadicam):
  * rosto centralizado na horizontal; OLHOS no terço superior (eye_y do enquadramento);
  * tamanho do recorte definido pelo enquadramento (closeup/medium/wide = fração da largura
    ocupada pelo rosto), com zoom suavizado e zona morta (só muda se a diferença passar de 10%);
  * a "câmera" só se move quando o rosto sai da zona morta (5% do recorte) e então acompanha
    com easing exponencial e velocidade máxima limitada — nunca "jitter";
  * nunca sai dos limites do quadro;
  * recorte menor que 1080 px é ampliado com Lanczos + leve unsharp (só luma).

fit:  cover   = recorte 9:16 de altura cheia (tela cheia; rosto pode ficar maior que o alvo)
      smart   = recorte do tamanho pedido; se for mais "baixo" que 9:16, entra sobre fundo desfocado
      contain = vídeo inteiro sobre fundo desfocado (sem rastreamento)
      auto    = cover se o rosto couber com ≤ 55% da largura final; senão smart
--static: um único recorte fixo (mediana da trajetória).

Entrada: polish.json (+ analysis.json na mesma pasta). Saída: reframe.mkv (só vídeo) + track.json.

Uso:
  python scripts/02_reframe.py work/<nome>/polish/polish.json
  python scripts/02_reframe.py polish.json --framing closeup --fit cover --static
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402

MAX_UPSCALE = 2.5       # recorte nunca menor que 1080/2.5 px de largura
DEAD_ZONE = 0.05        # fração do recorte
ZOOM_DEAD = 0.10        # zona morta do zoom (10%)
EASE_POS = 0.06         # por quadro (constante de tempo ≈ 0,55 s)
EASE_ZOOM = 0.03
MAX_SPEED = 0.012       # fração do recorte por quadro


def follower(target, dead, ease, max_step):
    """Zona morta + easing exponencial + velocidade máxima. target: array por quadro."""
    out = np.empty_like(target)
    cur = float(target[0])
    for i, tg in enumerate(target):
        err = tg - cur
        d = dead[i] if hasattr(dead, "__len__") else dead
        if abs(err) > d:
            step = (err - np.sign(err) * d) * ease
            lim = max_step[i] if hasattr(max_step, "__len__") else max_step
            step = float(np.clip(step, -lim, lim))
            cur += step
        out[i] = cur
    return out


def plan(cfg, analysis, info, n_frames, framing, fit, static):
    """Calcula, por quadro, o retângulo de recorte na origem (x, y, w, h) e o modo de ajuste."""
    W, H = info["width"], info["height"]
    tr = analysis.get("track") or {}
    fr = pc.FRAMINGS[framing]
    maxw = min(W, H * 9 / 16)
    t_frames = np.arange(n_frames) / pc.FPS
    if fit == "contain" or not tr.get("cx"):
        mode = "contain" if fit == "contain" else "cover-center"
        if fit != "contain":
            pc.log("aviso: sem trajetória de rosto — recorte central fixo")
        cw = W if mode == "contain" else maxw
        ch = H if mode == "contain" else cw * 16 / 9
        rect = np.tile([(W - cw) / 2, (H - ch) / 2, cw, ch], (n_frames, 1))
        return rect, mode, {"warnings": []}

    ts = np.array(tr["t"])
    cx = np.interp(t_frames, ts, tr["cx"])
    fw = np.interp(t_frames, ts, tr["w"])
    ey = np.interp(t_frames, ts, tr["eye_y"])
    desired_w = fw / fr["face_ratio"]
    desired_w = np.clip(desired_w, pc.OUT_W / MAX_UPSCALE, W)
    med_desired = float(np.median(desired_w))
    if fit == "auto":
        if med_desired <= maxw * 1.001:
            mode = "cover"
        else:
            face_in_cover = float(np.median(fw)) / maxw
            mode = "cover" if face_in_cover <= 0.55 else "smart"
    else:
        mode = fit
    if mode == "cover":
        crop_w = np.minimum(desired_w, maxw)
    else:  # smart
        crop_w = desired_w
    if static:
        crop_w = np.full(n_frames, float(np.median(crop_w)))
    else:
        crop_w = follower(crop_w, crop_w * ZOOM_DEAD, EASE_ZOOM, crop_w * 0.004)
    crop_h = np.minimum(crop_w * 16 / 9, H)
    # posição-alvo: rosto no centro horizontal; olhos em eye_y do recorte
    tx = cx - crop_w / 2
    ty = ey - fr["eye_y"] * crop_h
    if static:
        tx = np.full(n_frames, float(np.median(tx)))
        ty = np.full(n_frames, float(np.median(ty)))
    else:
        tx = follower(tx, crop_w * DEAD_ZONE, EASE_POS, crop_w * MAX_SPEED)
        ty = follower(ty, crop_h * DEAD_ZONE, EASE_POS, crop_h * MAX_SPEED)
    tx = np.clip(tx, 0, W - crop_w)
    ty = np.clip(ty, 0, H - crop_h)
    rect = np.stack([tx, ty, crop_w, crop_h], axis=1)
    warns = []
    up = pc.OUT_W / float(np.median(crop_w))
    if up > 1.6:
        warns.append(f"ampliação de {up:.2f}× (recorte mediano {np.median(crop_w):.0f} px) — perde um pouco de nitidez")
    return rect, mode, {"warnings": warns, "upscale_median": round(up, 3)}


def make_bg(crop_bgr, out_w, out_h):
    import cv2
    small = cv2.resize(crop_bgr, (out_w // 8, out_h // 8), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 6)
    bg = cv2.resize(small, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    return (bg.astype(np.float32) * 0.62).astype(np.uint8)


def luma_unsharp(bgr, amount, sigma=1.1):
    import cv2
    if amount <= 0:
        return bgr
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    y = ycc[..., 0]
    bl = cv2.GaussianBlur(y, (0, 0), sigma)
    ycc[..., 0] = cv2.addWeighted(y, 1 + amount, bl, -amount, 0)
    return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)


def main():
    ap = argparse.ArgumentParser(description="Recorte 9:16 dinâmico seguindo o rosto.")
    ap.add_argument("polish", help="polish.json")
    ap.add_argument("--workdir", help="pasta de trabalho (padrão: pasta do polish.json)")
    ap.add_argument("--framing", choices=list(pc.FRAMINGS), help="sobrescreve o enquadramento")
    ap.add_argument("--fit", choices=["auto", "cover", "smart", "contain"], help="sobrescreve o fit")
    ap.add_argument("--static", action="store_true", help="recorte fixo (mediana)")
    ap.add_argument("--out", help="saída (padrão: <workdir>/reframe.mkv)")
    ap.add_argument("--crf", type=int, default=12)
    args = ap.parse_args()

    import cv2
    cfg_path = Path(args.polish).resolve()
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    src = pc.polish_source(cfg, cfg_path)
    if not P["analysis"].exists():
        pc.die(f"analysis.json não encontrado em {P['analysis']} — rode 01_analyze.py antes")
    analysis = pc.load_json(P["analysis"])
    info = pc.probe(src)
    framing = args.framing or cfg["framing"]
    fit = args.fit or cfg["fit"]
    static = args.static or bool(cfg["static"])
    out = Path(args.out) if args.out else P["reframe"]
    W, H = info["width"], info["height"]
    n_frames = int(round((info["duration"] or 0) * pc.FPS))
    rect, mode, extra = plan(cfg, analysis, info, max(n_frames, 1), framing, fit, static)
    for w in extra["warnings"]:
        pc.log(f"aviso: {w}")
    pc.log(f"reenquadrando {src.name}: {W}×{H} -> {pc.OUT_W}×{pc.OUT_H}, enquadramento {framing}, "
           f"fit {mode}{' (estático)' if static else ''}, recorte mediano "
           f"{np.median(rect[:, 2]):.0f}×{np.median(rect[:, 3]):.0f} px ...")

    reader = pc.FrameReader(src, fps=pc.FPS)
    writer = pc.FrameWriter(out, pc.OUT_W, pc.OUT_H, crf=args.crf)
    i = 0
    rects_out = []
    for frame in reader:
        r = rect[min(i, len(rect) - 1)]
        x, y, cw, ch = float(r[0]), float(r[1]), float(r[2]), float(r[3])
        iw, ih = int(round(cw)), int(round(ch))
        sub = cv2.getRectSubPix(frame, (iw, ih), (x + cw / 2, y + ch / 2))
        up = pc.OUT_W / iw
        interp = cv2.INTER_LANCZOS4 if up > 1.05 else cv2.INTER_AREA
        if mode in ("cover", "cover-center") and abs(cw * 16 / 9 - ch) < 2:
            frame_out = cv2.resize(sub, (pc.OUT_W, pc.OUT_H), interpolation=interp)
        else:
            # recorte mais "baixo" que 9:16 (smart/contain): largura cheia sobre fundo desfocado
            oh = int(round(pc.OUT_W * ih / iw / 2)) * 2
            oh = min(oh, pc.OUT_H)
            fg = cv2.resize(sub, (pc.OUT_W, oh), interpolation=interp)
            frame_out = make_bg(sub, pc.OUT_W, pc.OUT_H)
            y0 = (pc.OUT_H - oh) // 2
            frame_out[y0:y0 + oh] = fg
        if up > 1.2:
            frame_out = luma_unsharp(frame_out, min(0.35, 0.25 * (up - 1)))
        writer.write(frame_out)
        if i % 300 == 0:
            rects_out.append({"frame": i, "x": round(x, 1), "y": round(y, 1), "w": round(cw, 1), "h": round(ch, 1)})
        i += 1
    writer.close()
    if i == 0:
        pc.die("nenhum quadro lido")
    track = {"source": str(src), "fps": pc.FPS, "frames": i, "framing": framing, "fit": mode, "static": static,
             "out": [pc.OUT_W, pc.OUT_H], "upscale_median": extra.get("upscale_median"),
             "crop": [[round(float(v), 2) for v in rect[min(k, len(rect) - 1)]] for k in range(i)],
             "samples": rects_out, "warnings": extra["warnings"]}
    pc.save_json(P["track"], track)
    pc.log(f"OK: {out} ({i} quadros)  |  trajetória: {P['track']}")


if __name__ == "__main__":
    main()
