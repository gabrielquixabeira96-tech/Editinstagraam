#!/usr/bin/env python3
"""08 — QA automático do polished.mp4 -> qa_report.json (aprovado/reprovado) + compare_sheet.png.

Checagens:
  1. duração      — mesma do original (±1 quadro) e mesmo número de quadros (timeline intacta);
  2. formato      — 1080×1920, 30 fps, H.264 + AAC;
  3. enquadramento— rosto em % da largura e olhos na zona-alvo (18–55% da altura; centro x 28–72%,
                    folga para a zona morta e o atraso proposital da "câmera" em movimentos rápidos)
                    em ≥ 95% dos quadros amostrados (só em mode raw com rosto rastreado);
  4. clipping     — luma estourada não acima do original (+0,5 pp);
  5. nitidez      — rosto no polido ≥ 0,95× o original (escala normalizada); com ampliação do recorte
                    > 1,5× exige 0,85×, > 2× exige 0,75× (ampliar não inventa detalhe);
  6. sincronia    — offset do áudio em relação ao original = 0 (±1 ms, correlação de envelope);
  7. pele natural — variância de textura fina na pele ≥ 60% da original (sem "pele de plástico").

Uso:
  python scripts/08_qa.py work/<nome>/polish/polish.json
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402

N_INSTANTS = 6
EYE_ZONE = (0.18, 0.55)
CX_ZONE = (0.28, 0.72)


def _mod(name):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), Path(__file__).with_name(name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def grab(path, t):
    rd = pc.FrameReader(path, start=t, dur=0.05)
    fr = next(iter(rd), None)
    rd.close()
    return fr


def skin_texture(bgr, face):
    """Variância do detalhe fino (ROI do rosto normalizada a 256 px) dentro da máscara de cor de pele."""
    import cv2
    x, y, w, h = face["x"], face["y"], face["w"], face["h"]
    roi = bgr[max(0, int(y)):int(y + h), max(0, int(x)):int(x + w)]
    if roi.size == 0:
        return None
    s = 256 / roi.shape[1]
    roi = cv2.resize(roi, (256, max(8, int(roi.shape[0] * s))), interpolation=cv2.INTER_AREA)
    ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    Y, Cr, Cb = ycc[..., 0], ycc[..., 1], ycc[..., 2]
    m = (Cr > 135) & (Cr < 180) & (Cb > 85) & (Cb < 135) & (Y > 60)
    if m.sum() < 200:
        return None
    hp = Y - cv2.GaussianBlur(Y, (0, 0), 1.5)
    return float(hp[m].var())


def main():
    ap = argparse.ArgumentParser(description="QA do polished.mp4.")
    ap.add_argument("polish")
    ap.add_argument("--workdir")
    ap.add_argument("--final", help="vídeo polido (padrão: polished.mp4)")
    ap.add_argument("--out", help="relatório (padrão: qa_report.json)")
    args = ap.parse_args()

    import cv2
    an = _mod("01_analyze.py")
    au = _mod("06_audio_polish.py")
    cfg_path = Path(args.polish).resolve()
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    src = pc.polish_source(cfg, cfg_path)
    final = Path(args.final) if args.final else P["final"]
    out = Path(args.out) if args.out else P["qa"]
    if not final.exists():
        pc.die(f"polished.mp4 não encontrado: {final}")
    si, fi = pc.probe(src), pc.probe(final)
    analysis = pc.load_json(P["analysis"]) if P["analysis"].exists() else {}
    checks, defects, warns = {}, [], []

    # 1. duração / quadros
    exp_frames = int(round((si["duration"] or 0) * pc.FPS))
    got_frames = pc.count_frames(final)
    d_ok = abs((fi["duration"] or 0) - (si["duration"] or 0)) <= 1.5 / pc.FPS and abs(got_frames - exp_frames) <= 1
    checks["duracao"] = {"ok": d_ok, "original_s": si["duration"], "polido_s": fi["duration"],
                         "quadros_esperados": exp_frames, "quadros": got_frames}
    if not d_ok:
        defects.append(f"duração/quadros diferem do original ({si['duration']:.3f}s/{exp_frames} vs "
                       f"{fi['duration']:.3f}s/{got_frames})")

    # 2. formato
    f_ok = (fi["width"], fi["height"]) == (pc.OUT_W, pc.OUT_H) and abs((fi["fps"] or 0) - pc.FPS) < 0.01 \
        and fi["vcodec"] == "h264" and fi["acodec"] == "aac"
    checks["formato"] = {"ok": f_ok, "largura": fi["width"], "altura": fi["height"], "fps": fi["fps"],
                         "video": fi["vcodec"], "audio": fi["acodec"]}
    if not f_ok:
        defects.append("formato fora do padrão 1080×1920 @ 30 fps H.264/AAC")

    # 3. enquadramento (amostra o polido a 3 fps)
    det = pc.FaceDetector()
    rd = pc.FrameReader(final, width=540, fps=3)
    sc = rd.src_w / rd.w
    faces, prev = [], None
    for fr in rd:
        f = pc.pick_face(det.detect(fr), prev)
        if f:
            prev = f
            faces.append({k: f[k] * sc for k in ("x", "y", "w", "h")} | {"eye_y": np.mean([e[1] for e in f["eyes"]]) * sc})
        else:
            faces.append(None)
    det_frac = np.mean([f is not None for f in faces]) if faces else 0.0
    tracked = cfg["mode"] == "raw" and bool((analysis.get("track") or {}).get("cx"))
    if faces and any(faces):
        fw = np.array([f["w"] for f in faces if f]) / pc.OUT_W
        ey = np.array([f["eye_y"] for f in faces if f]) / pc.OUT_H
        cx = np.array([f["x"] + f["w"] / 2 for f in faces if f]) / pc.OUT_W
        in_zone = ((ey >= EYE_ZONE[0]) & (ey <= EYE_ZONE[1]) & (cx >= CX_ZONE[0]) & (cx <= CX_ZONE[1])).mean()
        e_ok = bool(in_zone >= 0.95) if tracked else True
        before = (analysis.get("face") or {}).get("width_frac_in_cover_crop")
        checks["enquadramento"] = {"ok": e_ok, "rosto_detectado_frac": round(float(det_frac), 3),
                                   "rosto_pct_largura_mediana": round(100 * float(np.median(fw)), 1),
                                   "rosto_pct_largura_original_cover": None if before is None else round(100 * before, 1),
                                   "olhos_y_mediana": round(float(np.median(ey)), 3),
                                   "centro_x_mediana": round(float(np.median(cx)), 3),
                                   "na_zona_alvo_frac": round(float(in_zone), 3), "avaliado": tracked}
        if not e_ok:
            defects.append(f"rosto fora da zona-alvo em {100 * (1 - in_zone):.0f}% dos quadros amostrados")
    else:
        checks["enquadramento"] = {"ok": not tracked, "rosto_detectado_frac": 0.0, "avaliado": tracked}
        if tracked:
            defects.append("nenhum rosto detectado no vídeo polido")
        else:
            warns.append("nenhum rosto detectado no vídeo polido (enquadramento não avaliado)")

    # 4–7. comparações por instante
    dur = si["duration"] or 0.0
    times = [dur * (i + 0.5) / N_INSTANTS for i in range(N_INSTANTS)] if dur > 1 else [0.0]
    tr = analysis.get("track") or {}
    clip_o, clip_p, sh_o, sh_p, tx_o, tx_p = [], [], [], [], [], []
    thumbs_o, thumbs_p = [], []
    for t in times:
        fo, fp = grab(src, t), grab(final, t)
        if fo is None or fp is None:
            continue
        eo = an.exposure_stats(fo)
        ep = an.exposure_stats(fp)
        clip_o.append(eo["clip_high"]); clip_p.append(ep["clip_high"])
        # rosto no original (trajetória da análise) e no polido (detector)
        face_o = None
        if tr.get("cx"):
            k = int(np.clip(round(t * tr["fps"]), 0, len(tr["t"]) - 1))
            face_o = {"x": tr["cx"][k] - tr["w"][k] / 2, "y": tr["cy"][k] - tr["h"][k] / 2, "w": tr["w"][k], "h": tr["h"][k]}
        fp_small = cv2.resize(fp, (540, 960), interpolation=cv2.INTER_AREA)
        fl = det.detect(fp_small)
        face_p = {k: fl[0][k] * 2 for k in ("x", "y", "w", "h")} if fl else None
        go, gp = cv2.cvtColor(fo, cv2.COLOR_BGR2GRAY), cv2.cvtColor(fp, cv2.COLOR_BGR2GRAY)
        if face_o and face_p:
            ro = go[max(0, int(face_o["y"])):int(face_o["y"] + face_o["h"]), max(0, int(face_o["x"])):int(face_o["x"] + face_o["w"])]
            rp = gp[max(0, int(face_p["y"])):int(face_p["y"] + face_p["h"]), max(0, int(face_p["x"])):int(face_p["x"] + face_p["w"])]
            so, sp = an.sharpness(ro), an.sharpness(rp)
            if so and sp:
                sh_o.append(so); sh_p.append(sp)
            to, tp = skin_texture(fo, face_o), skin_texture(fp, face_p)
            if to and tp:
                tx_o.append(to); tx_p.append(tp)
        elif cfg["mode"] == "final" and face_p:
            pass
        thumbs_o.append((t, fo)); thumbs_p.append((t, fp))

    c_ok = (np.mean(clip_p) <= np.mean(clip_o) + 0.005) if clip_p else True
    checks["clipping"] = {"ok": bool(c_ok), "original_pct": round(100 * float(np.mean(clip_o)), 3) if clip_o else None,
                          "polido_pct": round(100 * float(np.mean(clip_p)), 3) if clip_p else None}
    if not c_ok:
        defects.append("luma estourada acima do original")

    if sh_o:
        ratio = float(np.median(sh_p) / max(np.median(sh_o), 1e-6))
        up = float((pc.load_json(P["track"]).get("upscale_median") or 1.0) if P["track"].exists() else 1.0)
        # ampliar não inventa detalhe: a exigência cai com o fator de ampliação do recorte
        thr = 0.95 if up <= 1.5 else (0.85 if up <= 2.0 else 0.75)
        s_ok = bool(ratio >= thr)
        checks["nitidez"] = {"ok": s_ok, "rosto_original": round(float(np.median(sh_o)), 1),
                             "rosto_polido": round(float(np.median(sh_p)), 1), "razao": round(ratio, 3),
                             "ampliacao": round(up, 2), "minimo_exigido": thr}
        if not s_ok:
            defects.append(f"nitidez do rosto caiu para {ratio:.2f}× a original (mínimo {thr}; aumente `sharpen` ou use closeup)")
    else:
        checks["nitidez"] = {"ok": True, "avaliado": False}
        warns.append("nitidez não comparada (sem rosto em ambos)")

    lag = au.measure_lag(src, final) if si["has_audio"] and fi["has_audio"] else 0.0
    a_ok = abs(lag) <= 0.0015
    checks["sincronia_audio"] = {"ok": a_ok, "offset_ms": round(lag * 1000, 2)}
    if not a_ok:
        defects.append(f"áudio deslocado {lag * 1000:+.1f} ms em relação ao original")

    if tx_o:
        tr_ = float(np.median(tx_p) / max(np.median(tx_o), 1e-6))
        t_ok = tr_ >= 0.60
        checks["pele_natural"] = {"ok": t_ok, "textura_original": round(float(np.median(tx_o)), 2),
                                  "textura_polida": round(float(np.median(tx_p)), 2), "razao": round(tr_, 3)}
        if not t_ok:
            defects.append(f"textura da pele caiu para {tr_:.2f}× (pele 'plástica') — reduza beauty_intensity")
    else:
        checks["pele_natural"] = {"ok": True, "avaliado": False}

    # folha de comparação
    if thumbs_o:
        tw = 320
        row_o, row_p = [], []
        for (t, fo), (_, fp) in zip(thumbs_o, thumbs_p):
            ho = int(tw * fo.shape[0] / fo.shape[1])
            a = cv2.resize(fo, (tw, ho), interpolation=cv2.INTER_AREA)
            b = cv2.resize(fp, (tw, int(tw * 16 / 9)), interpolation=cv2.INTER_AREA)
            cv2.putText(a, pc.fmt_t(t), (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
            row_o.append(a); row_p.append(b)
        hmax = max(x.shape[0] for x in row_o)
        row_o = [np.pad(x, ((0, hmax - x.shape[0]), (0, 0), (0, 0))) for x in row_o]
        top, bot = np.hstack(row_o), np.hstack(row_p)
        label = np.zeros((30, top.shape[1], 3), np.uint8) + 25
        cv2.putText(label, "ANTES (original)", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2, cv2.LINE_AA)
        label2 = label.copy()
        cv2.putText(label2, "DEPOIS (polished.mp4)", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2, cv2.LINE_AA)
        label2[:, :] = 25
        cv2.putText(label2, "DEPOIS (polished.mp4)", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2, cv2.LINE_AA)
        cv2.imwrite(str(P["compare_sheet"]), np.vstack([label, top, label2, bot]))

    status = "aprovado" if not defects else "reprovado"
    rep = {"status": status, "video_final": str(final), "defeitos": defects, "avisos": warns, "checagens": checks}
    pc.save_json(out, rep)
    pc.log(f"QA: {status.upper()}  ({out})")
    for d in defects:
        pc.log(f"  ✗ {d}")
    for w in warns:
        pc.log(f"  aviso: {w}")
    sys.exit(0 if status == "aprovado" else 2)


if __name__ == "__main__":
    main()
