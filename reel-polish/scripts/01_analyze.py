#!/usr/bin/env python3
"""01 — Analisa o vídeo para o Claude decidir o `polish.json`.

Mede (amostrando o vídeo a 5 fps, reduzido a 960 px de largura):
  * rosto por quadro (YuNet; Haar de reserva), rastreado e suavizado (Savitzky-Golay);
  * exposição (histograma de luma, clipping, luma do rosto), balanço de branco (neutros e pele);
  * nitidez (variância do Laplaciano no rosto, escala normalizada) e ruído (Immerkær) em quadros cheios;
  * tremor da câmera (correlação de fase entre quadros consecutivos em janelas de 1 s);
  * áudio: piso de ruído, nível de fala, SNR, sibilância, graves/rumble, clipping.

Saídas em work/<nome>/polish/: analysis.json, analysis.txt (legível, com sugestões) e
analysis_sheet.png (quadros amostrados com as caixas de rosto).

Uso:
  python scripts/01_analyze.py <video>                      # work/<nome>/polish/
  python scripts/01_analyze.py <video> --workdir <pasta> [--fps 5] [--detector yunet|haar]
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402


# ---------------------------------------------------------------- medidas de imagem
def luma_of(bgr):
    b, g, r = bgr[..., 0].astype(np.float32), bgr[..., 1].astype(np.float32), bgr[..., 2].astype(np.float32)
    return 0.114 * b + 0.587 * g + 0.299 * r


def exposure_stats(bgr, face=None):
    Y = luma_of(bgr)
    st = {"mean": float(Y.mean() / 255), "p1": float(np.percentile(Y, 1) / 255),
          "p50": float(np.percentile(Y, 50) / 255), "p99": float(np.percentile(Y, 99) / 255),
          "clip_low": float((Y <= 2).mean()), "clip_high": float((Y >= 253).mean())}
    if face is not None:
        x, y, w, h = face["x"], face["y"], face["w"], face["h"]
        ys, ye = int(y + 0.25 * h), int(y + 0.75 * h)
        xs, xe = int(x + 0.25 * w), int(x + 0.75 * w)
        patch = Y[max(0, ys):max(1, ye), max(0, xs):max(1, xe)]
        st["face"] = float(patch.mean() / 255) if patch.size else None
    else:
        st["face"] = None
    return st


def wb_stats(bgr, face=None):
    import cv2
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    Y, Cr, Cb = ycc[..., 0], ycc[..., 1] - 128, ycc[..., 2] - 128
    chroma = np.sqrt(Cr ** 2 + Cb ** 2)
    neutral = (chroma < 14) & (Y > 60) & (Y < 225)
    out = {"neutral_frac": float(neutral.mean())}
    if neutral.sum() > 200:
        out["neutral_cb"] = float(Cb[neutral].mean())
        out["neutral_cr"] = float(Cr[neutral].mean())
    else:
        out["neutral_cb"] = out["neutral_cr"] = None
    if face is not None:
        x, y, w, h = face["x"], face["y"], face["w"], face["h"]
        ys, ye = int(y + 0.3 * h), int(y + 0.7 * h)
        xs, xe = int(x + 0.3 * w), int(x + 0.7 * w)
        p = bgr[max(0, ys):max(1, ye), max(0, xs):max(1, xe)].reshape(-1, 3).astype(np.float32)
        if p.size:
            b, g, r = p[:, 0].mean(), p[:, 1].mean(), p[:, 2].mean()
            out["skin_rgb"] = [float(r), float(g), float(b)]
            out["skin_r_over_b"] = float(r / max(b, 1.0))
            out["skin_cr_minus_cb"] = float((Cr[ys:ye, xs:xe]).mean() - (Cb[ys:ye, xs:xe]).mean())
    return out


def sharpness(gray_roi, norm_width=256):
    import cv2
    if gray_roi.size == 0:
        return None
    h, w = gray_roi.shape
    s = norm_width / float(w)
    g = cv2.resize(gray_roi, (norm_width, max(8, int(round(h * s)))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def noise_sigma(gray):
    """Estimativa de ruído de Immerkær (desvio-padrão em níveis 0–255)."""
    import cv2
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
    h, w = gray.shape
    r = cv2.filter2D(gray.astype(np.float32), -1, k)
    return float(np.sum(np.abs(r)) * math.sqrt(0.5 * math.pi) / (6.0 * (w - 2) * (h - 2)))


# ---------------------------------------------------------------- tremor
def measure_shake(src, duration, n_windows=8, win_s=1.0, width=480):
    import cv2
    duration = max(duration, 0.1)
    times = [duration * (i + 0.5) / n_windows for i in range(n_windows)] if duration > win_s * 2 else [0.0]
    jitters, drifts = [], []
    for t0 in times:
        rd = pc.FrameReader(src, width=width, start=max(0.0, t0 - win_s / 2), dur=win_s, gray=True)
        prev, shifts = None, []
        for f in rd:
            g = f.astype(np.float32)
            if prev is not None:
                (dx, dy), _ = cv2.phaseCorrelate(prev, g)
                shifts.append((dx, dy))
            prev = g
        if len(shifts) < 6:
            continue
        s = np.array(shifts)
        pos = np.cumsum(s, axis=0)
        trend = np.stack([pc.smooth(pos[:, 0], 15), pc.smooth(pos[:, 1], 15)], axis=1)
        jit = pos - trend
        jitters.append(float(np.sqrt((jit ** 2).sum(axis=1).mean())))
        drifts.append(float(np.abs(s).mean()))
    if not jitters:
        return {"jitter_px": 0.0, "jitter_pct_width": 0.0, "windows": 0}
    j = float(np.median(jitters))
    return {"jitter_px": round(j, 3), "jitter_pct_width": round(100 * j / width, 3),
            "windows": len(jitters), "per_window_px": [round(x, 2) for x in jitters]}


# ---------------------------------------------------------------- áudio
def analyze_audio(src, info):
    if not info["has_audio"]:
        return {"present": False}
    from scipy.signal import welch
    sr = 16000
    a = pc.decode_audio(src, sr=sr, channels=1)[:, 0]
    if len(a) < sr:
        return {"present": True, "too_short": True}
    frame = int(0.05 * sr)
    n = len(a) // frame
    fr = a[:n * frame].reshape(n, frame)
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    floor = float(np.percentile(db, 10))
    speech = float(np.percentile(db, 90))
    loud = fr[db > speech - 6]
    sib = low = None
    if len(loud) > 10:
        x = loud.reshape(-1)
        f, P = welch(x, fs=sr, nperseg=1024)
        e = lambda lo, hi: float(P[(f >= lo) & (f < hi)].sum() + 1e-12)  # noqa: E731
        sib = 10 * math.log10(e(5000, 8000) / e(1000, 4000))
        low = 10 * math.log10(e(20, 80) / e(80, 4000))
    peak = float(np.abs(a).max())
    return {"present": True, "noise_floor_db": round(floor, 1), "speech_db": round(speech, 1),
            "snr_db": round(speech - floor, 1), "sibilance_db": None if sib is None else round(sib, 1),
            "rumble_db": None if low is None else round(low, 1),
            "peak_dbfs": round(20 * math.log10(peak + 1e-9), 1), "clip_frac": float((np.abs(a) > 0.985).mean())}


# ---------------------------------------------------------------- folha de contato
def contact_sheet(src, samples, out_path, cols=4, rows=3, thumb_w=480):
    import cv2
    if not samples:
        return
    idx = np.linspace(0, len(samples) - 1, cols * rows).round().astype(int)
    chosen = [samples[i] for i in idx]
    info = pc.probe(src)
    th = int(round(thumb_w * info["height"] / info["width"]))
    sheet = np.zeros((rows * (th + 28), cols * thumb_w, 3), dtype=np.uint8) + 18
    for k, s in enumerate(chosen):
        rd = pc.FrameReader(src, width=thumb_w, start=s["t"], dur=0.05)
        frame = next(iter(rd), None)
        rd.close()
        if frame is None:
            continue
        frame = cv2.resize(frame, (thumb_w, th))
        sc = thumb_w / info["width"]
        if s.get("face"):
            f = s["face"]
            x, y, w, h = int(f["x"] * sc), int(f["y"] * sc), int(f["w"] * sc), int(f["h"] * sc)
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 230, 90), 2)
            for (ex, ey) in f.get("eyes", []):
                cv2.circle(frame, (int(ex * sc), int(ey * sc)), 3, (0, 200, 255), -1)
        r, c = divmod(k, cols)
        y0, x0 = r * (th + 28), c * thumb_w
        sheet[y0:y0 + th, x0:x0 + thumb_w] = frame
        lab = f"{pc.fmt_t(s['t'])}  luma {s['exposure']['mean']:.2f}"
        if s.get("face"):
            lab += f"  rosto {100 * s['face']['w'] / info['width']:.0f}% larg."
        else:
            lab += "  sem rosto"
        cv2.putText(sheet, lab, (x0 + 8, y0 + th + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA)
    cv2.imwrite(str(out_path), sheet)


# ---------------------------------------------------------------- relatório
def build_report(info, samples, track, shake, audio, noise, sharp, detector):
    W, H = info["width"], info["height"]
    n = len(samples)
    det = [s for s in samples if s.get("face")]
    rep = {"video": {"path": info["path"], "width": W, "height": H, "fps": info["fps"], "duration": info["duration"],
                     "has_audio": info["has_audio"], "rotation": info["rotation"]},
           "detector": detector, "sampled_frames": n, "face_detected_frames": len(det),
           "face_detected_frac": (len(det) / n) if n else 0.0}
    cover_w = H * 9 / 16 if W / H > 9 / 16 else W           # largura do recorte 9:16 "cover" na origem
    if det:
        fw = np.array([s["face"]["w"] for s in det])
        cx = np.array([s["face"]["x"] + s["face"]["w"] / 2 for s in det])
        cy = np.array([s["face"]["y"] + s["face"]["h"] / 2 for s in det])
        ey = np.array([np.mean([e[1] for e in s["face"]["eyes"]]) for s in det])
        rep["face"] = {
            "width_px_median": float(np.median(fw)), "width_frac_of_source": float(np.median(fw) / W),
            "width_frac_in_cover_crop": float(np.median(fw) / cover_w),
            "center_x_frac": [float(np.percentile(cx, 5) / W), float(np.median(cx) / W), float(np.percentile(cx, 95) / W)],
            "center_y_frac": [float(np.percentile(cy, 5) / H), float(np.median(cy) / H), float(np.percentile(cy, 95) / H)],
            "eyes_y_frac_median": float(np.median(ey) / H),
            "motion_range_px": [float(np.percentile(cx, 95) - np.percentile(cx, 5)),
                                float(np.percentile(cy, 95) - np.percentile(cy, 5))],
            "size_variation": float(np.percentile(fw, 95) / max(np.percentile(fw, 5), 1.0)),
        }
        # enquadramento alcançável sem barras: ratio do rosto no recorte "cover" (altura cheia)
        ach = {}
        for name, fr in pc.FRAMINGS.items():
            need_w = np.median(fw) / fr["face_ratio"]
            ach[name] = {"crop_width_px": float(need_w), "fits_without_bars": bool(need_w * 16 / 9 <= H + 1e-6)
                         if W / H > 9 / 16 else bool(need_w <= W + 1e-6)}
        rep["face"]["framing_feasibility"] = ach
    else:
        rep["face"] = None
    ex = [s["exposure"] for s in samples]
    rep["exposure"] = {
        "mean": float(np.mean([e["mean"] for e in ex])), "p1": float(np.mean([e["p1"] for e in ex])),
        "p99": float(np.mean([e["p99"] for e in ex])), "clip_low": float(np.mean([e["clip_low"] for e in ex])),
        "clip_high": float(np.mean([e["clip_high"] for e in ex])),
        "face_luma": float(np.mean([e["face"] for e in ex if e.get("face") is not None])) if det else None,
        "variation_over_time": float(np.std([e["mean"] for e in ex])),
    }
    wb = [s["wb"] for s in samples]
    ncb = [w["neutral_cb"] for w in wb if w.get("neutral_cb") is not None]
    ncr = [w["neutral_cr"] for w in wb if w.get("neutral_cr") is not None]
    srb = [w["skin_r_over_b"] for w in wb if w.get("skin_r_over_b")]
    scc = [w["skin_cr_minus_cb"] for w in wb if w.get("skin_cr_minus_cb") is not None]
    cool_index = (np.mean(ncb) - np.mean(ncr)) if ncb else 0.0
    # calibração do filtro colortemperature do ffmpeg: ~1000 K ≈ 12 unidades de (Cr−Cb) (medido)
    suggested_shift = float(pc.clamp(round(cool_index * 80 / 50) * 50, -1500, 1500))
    rep["white_balance"] = {"neutral_cb": float(np.mean(ncb)) if ncb else None,
                            "neutral_cr": float(np.mean(ncr)) if ncr else None,
                            "cool_index": float(cool_index),
                            "skin_r_over_b": float(np.median(srb)) if srb else None,
                            "skin_cr_minus_cb": float(np.median(scc)) if scc else None,
                            "suggested_temperature_shift": suggested_shift}
    rep["sharpness"] = sharp
    rep["noise"] = noise
    rep["shake"] = shake
    rep["audio"] = audio
    rep["track"] = track
    return rep


def suggestions(rep):
    """Sugestões (o Claude pode seguir ou não) + rascunho de polish.json."""
    S = []
    face = rep.get("face")
    W, H = rep["video"]["width"], rep["video"]["height"]
    horizontal = W / H > 9 / 16 + 0.01
    pol = {"mode": "raw", "framing": "medium", "fit": "auto", "static": False, "stabilize": "auto",
           "look": "looks/natural.json", "beauty_intensity": 35}
    if not face or rep["face_detected_frac"] < 0.3:
        S.append("Rosto detectado em poucos quadros (<30%): o reenquadramento vai usar recorte central "
                 "(fallback). Considere `fit: contain` ou `static: true`.")
        pol["fit"] = "contain" if horizontal else "cover"
    else:
        r = face["width_frac_in_cover_crop"]
        feas = face["framing_feasibility"]
        if horizontal:
            S.append(f"Vídeo horizontal: o recorte 9:16 de altura cheia ('cover') deixa o rosto com "
                     f"{100 * r:.0f}% da largura final.")
            fits = [k for k, v in feas.items() if v["fits_without_bars"]]
            if fits:
                S.append(f"Enquadramentos possíveis sem barras: {', '.join(fits)}. "
                         f"Os demais usam 'smart' (recorte mais largo sobre fundo desfocado).")
            else:
                S.append("Nenhum enquadramento cabe sem barras: 'closeup' preenche a tela com o rosto grande; "
                         "'medium'/'wide' usam fundo desfocado em cima/embaixo (fit smart).")
            pol["framing"] = "medium" if r <= 0.62 else "closeup"
        else:
            S.append(f"Vídeo vertical: rosto com {100 * r:.0f}% da largura; o reenquadramento só reposiciona/aproxima.")
        if face["motion_range_px"][0] / W < 0.04 and face["motion_range_px"][1] / H < 0.04:
            S.append("Rosto quase parado: `static: true` (recorte fixo) é seguro e mais estável.")
            pol["static"] = True
        if face["size_variation"] > 1.35:
            S.append("Tamanho do rosto varia muito (aproxima/afasta): o zoom dinâmico vai acompanhar com suavização forte.")
    ex = rep["exposure"]
    fl = ex.get("face_luma")
    if fl is not None:
        if fl < 0.42:
            S.append(f"Rosto subexposto (luma {fl:.2f}): exposure.auto levanta para ~0.58 com proteção das altas luzes.")
        elif fl > 0.72:
            S.append(f"Rosto claro demais (luma {fl:.2f}): exposure.auto abaixa com cuidado; verifique clipping.")
        else:
            S.append(f"Exposição do rosto adequada (luma {fl:.2f}); só contraste suave.")
    if ex["clip_high"] > 0.01:
        S.append(f"{100 * ex['clip_high']:.1f}% dos pixels estourados (janelas/céu?): não dá para recuperar; "
                 "a curva protege o resto.")
    wb = rep["white_balance"]
    shift = wb["suggested_temperature_shift"]
    if abs(shift) >= 200:
        S.append(f"Balanço de branco {'frio/azulado' if shift > 0 else 'quente/amarelado'} "
                 f"(índice {wb['cool_index']:+.1f}): sugestão temperature_shift {shift:+.0f} K "
                 f"({'esquentar' if shift > 0 else 'esfriar'}).")
    else:
        S.append("Balanço de branco neutro; um toque quente (+200 K) costuma favorecer a pele.")
    pol["white_balance"] = {"auto": True, "temperature_shift": shift, "tint": 0.0, "strength": 0.7}
    sh = rep["shake"]
    if sh["jitter_pct_width"] > 0.25:
        S.append(f"Tremor {sh['jitter_pct_width']:.2f}% da largura (≈{sh['jitter_px']:.1f} px/480): estabilizar.")
        pol["stabilize"] = True
    else:
        S.append(f"Tremor baixo ({sh['jitter_pct_width']:.2f}% da largura): estabilização dispensável.")
        pol["stabilize"] = False
    nz = rep["noise"]["sigma"]
    if nz > 4.0:
        S.append(f"Ruído alto (σ≈{nz:.1f}): denoise 'medio' e beauty mais suave.")
        pol["denoise"] = "medio"
    elif nz > 2.0:
        S.append(f"Ruído moderado (σ≈{nz:.1f}): denoise 'leve'.")
        pol["denoise"] = "leve"
    else:
        S.append(f"Ruído baixo (σ≈{nz:.1f}): denoise mínimo.")
        pol["denoise"] = "leve"
    au = rep["audio"]
    if au.get("present") and not au.get("too_short"):
        if au["snr_db"] < 20:
            S.append(f"Áudio com muito ruído de fundo (SNR {au['snr_db']:.0f} dB, piso {au['noise_floor_db']:.0f} dB): "
                     "denoise forte (18 dB).")
            pol["audio"] = {"denoise_db": 18}
        elif au["snr_db"] < 35:
            S.append(f"Ruído de fundo moderado (SNR {au['snr_db']:.0f} dB): denoise padrão (12 dB).")
        else:
            S.append(f"Áudio limpo (SNR {au['snr_db']:.0f} dB): denoise leve (6 dB).")
            pol["audio"] = {"denoise_db": 6}
        if au.get("sibilance_db") is not None and au["sibilance_db"] > -6:
            S.append(f"Sibilância alta ({au['sibilance_db']:+.0f} dB em 5–8 kHz): de-esser 0.5.")
            pol.setdefault("audio", {})["deesser"] = 0.5
        if au.get("rumble_db") is not None and au["rumble_db"] > -10:
            S.append("Graves/rumble (<80 Hz) fortes: high-pass em 100 Hz.")
            pol.setdefault("audio", {})["highpass_hz"] = 100
        if au["clip_frac"] > 0.001:
            S.append("Áudio com clipping no original — não há como recuperar; evite ganho extra.")
    return S, pol


def write_txt(rep, S, pol, out):
    v, face, ex, wb = rep["video"], rep.get("face"), rep["exposure"], rep["white_balance"]
    L = []
    L.append(f"ANÁLISE — {Path(v['path']).name}")
    L.append(f"Vídeo: {v['width']}×{v['height']} @ {v['fps']:.3g} fps, {pc.fmt_t(v['duration'])}, "
             f"{'com' if v['has_audio'] else 'SEM'} áudio, rotação {v['rotation']}°; detector: {rep['detector']}")
    L.append("")
    L.append("ROSTO")
    if face:
        L.append(f"  detectado em {100 * rep['face_detected_frac']:.0f}% dos {rep['sampled_frames']} quadros amostrados")
        L.append(f"  largura mediana {face['width_px_median']:.0f} px = {100 * face['width_frac_of_source']:.0f}% da largura "
                 f"original; no recorte 9:16 'cover' = {100 * face['width_frac_in_cover_crop']:.0f}% da largura final")
        L.append(f"  centro x (p5/mediana/p95): {' / '.join(f'{100 * c:.0f}%' for c in face['center_x_frac'])};  "
                 f"centro y: {' / '.join(f'{100 * c:.0f}%' for c in face['center_y_frac'])};  olhos em y={100 * face['eyes_y_frac_median']:.0f}%")
        L.append(f"  deslocamento (p5–p95): {face['motion_range_px'][0]:.0f} px horizontal, {face['motion_range_px'][1]:.0f} px vertical; "
                 f"variação de tamanho ×{face['size_variation']:.2f}")
        for k, f in face["framing_feasibility"].items():
            L.append(f"  enquadramento {k:8s}: recorte de {f['crop_width_px']:.0f} px de largura — "
                     f"{'cabe sem barras' if f['fits_without_bars'] else 'NÃO cabe (usa fundo desfocado em fit smart)'}")
    else:
        L.append("  NENHUM rosto detectado de forma confiável")
    L.append("")
    L.append("EXPOSIÇÃO (0–1)")
    L.append(f"  luma média {ex['mean']:.2f}; p1 {ex['p1']:.2f}; p99 {ex['p99']:.2f}; "
             f"clipping escuro {100 * ex['clip_low']:.2f}%, claro {100 * ex['clip_high']:.2f}%; "
             f"rosto {ex['face_luma']:.2f}" if ex.get("face_luma") is not None else
             f"  luma média {ex['mean']:.2f}; p1 {ex['p1']:.2f}; p99 {ex['p99']:.2f}")
    L.append(f"  variação ao longo do tempo (desvio da média): {ex['variation_over_time']:.3f}")
    L.append("")
    L.append("BALANÇO DE BRANCO")
    L.append(f"  neutros: Cb {wb['neutral_cb'] if wb['neutral_cb'] is None else round(wb['neutral_cb'], 1)}, "
             f"Cr {wb['neutral_cr'] if wb['neutral_cr'] is None else round(wb['neutral_cr'], 1)} "
             f"(índice frio {wb['cool_index']:+.1f}; >0 = azulado)")
    if wb.get("skin_r_over_b"):
        L.append(f"  pele: R/B {wb['skin_r_over_b']:.2f} (referência 1,5–1,9), Cr−Cb {wb['skin_cr_minus_cb']:.0f} (referência 40–65)")
    L.append(f"  sugestão temperature_shift: {wb['suggested_temperature_shift']:+.0f} K (positivo = esquentar)")
    L.append("")
    L.append("NITIDEZ / RUÍDO / TREMOR")
    L.append(f"  nitidez do rosto (var. Laplaciano, 256 px): {rep['sharpness']['face']:.0f}"
             f"  |  quadro inteiro: {rep['sharpness']['frame']:.0f}" if rep["sharpness"].get("face") is not None
             else f"  nitidez do quadro: {rep['sharpness']['frame']:.0f}")
    L.append(f"  ruído σ ≈ {rep['noise']['sigma']:.2f} níveis (baixo <2, moderado 2–4, alto >4)")
    L.append(f"  tremor: {rep['shake']['jitter_px']:.2f} px RMS em 480 px = {rep['shake']['jitter_pct_width']:.2f}% da largura "
             f"(estabilizar se >0,25%)")
    L.append("")
    L.append("ÁUDIO")
    au = rep["audio"]
    if au.get("present") and not au.get("too_short"):
        L.append(f"  piso de ruído {au['noise_floor_db']:.0f} dBFS; fala {au['speech_db']:.0f} dBFS; SNR {au['snr_db']:.0f} dB; "
                 f"pico {au['peak_dbfs']:.1f} dBFS; clipping {100 * au['clip_frac']:.2f}%")
        L.append(f"  sibilância (5–8 kHz vs 1–4 kHz): {au['sibilance_db']:+.1f} dB (alto > −6);  "
                 f"rumble (<80 Hz): {au['rumble_db']:+.1f} dB (alto > −10)")
    else:
        L.append("  sem áudio")
    L.append("")
    L.append("SUGESTÕES")
    for s in S:
        L.append(f"  • {s}")
    L.append("")
    L.append("RASCUNHO DE polish.json (ajuste e salve em work/<nome>/polish/polish.json):")
    L.append(json.dumps(pol, ensure_ascii=False, indent=2))
    Path(out).write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Analisa rosto, exposição, cor, nitidez, tremor e áudio para o polish.json.")
    ap.add_argument("video")
    ap.add_argument("--workdir", help="pasta de saída (padrão: work/<nome>/polish/ ao lado do vídeo)")
    ap.add_argument("--fps", type=float, default=pc.ANALYSIS_FPS, help="taxa de amostragem (padrão 5)")
    ap.add_argument("--width", type=int, default=pc.ANALYSIS_WIDTH, help="largura reduzida da análise (padrão 960)")
    ap.add_argument("--detector", default="auto", choices=["auto", "yunet", "haar"])
    ap.add_argument("--no-sheet", action="store_true")
    args = ap.parse_args()

    import cv2
    src = Path(args.video).resolve()
    if not src.exists():
        pc.die(f"vídeo não encontrado: {src}")
    workdir = Path(args.workdir).resolve() if args.workdir else pc.default_workdir(src)
    workdir.mkdir(parents=True, exist_ok=True)
    P = pc.paths_in(workdir)
    info = pc.probe(src)
    info["path"] = str(src)
    if not info["width"]:
        pc.die("arquivo sem vídeo")
    W, H = info["width"], info["height"]
    det = pc.FaceDetector(args.detector)
    pc.log(f"analisando {src.name} ({W}×{H}, {pc.fmt_t(info['duration'] or 0)}) com detector {det.backend} ...")

    # 1) passada amostrada: rosto + exposição + WB
    rd = pc.FrameReader(src, width=args.width, fps=args.fps)
    sc = W / rd.w
    samples, prev = [], None
    for i, frame in enumerate(rd):
        t = i / args.fps
        faces = det.detect(frame)
        f = pc.pick_face(faces, prev)
        face = None
        if f:
            face = {k: (v * sc if isinstance(v, float) else v) for k, v in f.items() if k in ("x", "y", "w", "h")}
            face["score"] = f["score"]
            face["eyes"] = [(e[0] * sc, e[1] * sc) for e in f["eyes"]]
            face["nose"] = (f["nose"][0] * sc, f["nose"][1] * sc)
            face["mouth"] = [(m[0] * sc, m[1] * sc) for m in f["mouth"]]
            prev = f
        samples.append({"t": round(t, 3), "face": face, "exposure": exposure_stats(frame, f), "wb": wb_stats(frame, f)})
    if not samples:
        pc.die("não foi possível decodificar quadros")
    pc.log(f"  {len(samples)} quadros amostrados; rosto em {sum(1 for s in samples if s['face'])}")

    # 2) trajetória suavizada (em coordenadas da origem), com lacunas interpoladas
    ts = [s["t"] for s in samples]
    cx = pc.fill_gaps([None if not s["face"] else s["face"]["x"] + s["face"]["w"] / 2 for s in samples])
    cy = pc.fill_gaps([None if not s["face"] else s["face"]["y"] + s["face"]["h"] / 2 for s in samples])
    fw = pc.fill_gaps([None if not s["face"] else s["face"]["w"] for s in samples])
    fh = pc.fill_gaps([None if not s["face"] else s["face"]["h"] for s in samples])
    ey = pc.fill_gaps([None if not s["face"] else np.mean([e[1] for e in s["face"]["eyes"]]) for s in samples])
    has_face = not np.all(np.isnan(cx))
    win = int(round(args.fps * 2.0))  # 2 s de janela
    if has_face:
        track = {"fps": args.fps, "t": ts,
                 "cx": pc.smooth(cx, win).round(2).tolist(), "cy": pc.smooth(cy, win).round(2).tolist(),
                 "w": pc.smooth(fw, int(round(args.fps * 4.0))).round(2).tolist(),
                 "h": pc.smooth(fh, int(round(args.fps * 4.0))).round(2).tolist(),
                 "eye_y": pc.smooth(ey, win).round(2).tolist(),
                 "detected": [bool(s["face"]) for s in samples]}
    else:
        track = {"fps": args.fps, "t": ts, "cx": None, "cy": None, "w": None, "h": None, "eye_y": None,
                 "detected": [False] * len(samples)}

    # 3) quadros cheios: nitidez e ruído
    full_t = [info["duration"] * (i + 0.5) / 6 for i in range(6)] if (info["duration"] or 0) > 2 else [0.0]
    sharp_face, sharp_frame, sigmas = [], [], []
    for t in full_t:
        rd2 = pc.FrameReader(src, start=t, dur=0.05)
        fr = next(iter(rd2), None)
        rd2.close()
        if fr is None:
            continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        sigmas.append(noise_sigma(g))
        sharp_frame.append(sharpness(g, 512))
        if has_face:
            k = int(np.clip(round(t * args.fps), 0, len(ts) - 1))
            x0, y0 = int(track["cx"][k] - track["w"][k] / 2), int(track["cy"][k] - track["h"][k] / 2)
            x1, y1 = int(track["cx"][k] + track["w"][k] / 2), int(track["cy"][k] + track["h"][k] / 2)
            roi = g[max(0, y0):max(1, y1), max(0, x0):max(1, x1)]
            v = sharpness(roi)
            if v is not None:
                sharp_face.append(v)
    sharp = {"frame": float(np.median(sharp_frame)) if sharp_frame else 0.0,
             "face": float(np.median(sharp_face)) if sharp_face else None}
    noise = {"sigma": float(np.median(sigmas)) if sigmas else 0.0}

    # 4) tremor e áudio
    shake = measure_shake(src, info["duration"] or 0.0)
    audio = analyze_audio(src, info)

    rep = build_report(info, samples, track, shake, audio, noise, sharp, det.backend)
    rep["samples"] = samples
    S, pol = suggestions(rep)
    rep["suggestions"] = S
    rep["polish_draft"] = pol
    pc.save_json(P["analysis"], rep)
    write_txt(rep, S, pol, P["analysis_txt"])
    if not args.no_sheet:
        contact_sheet(src, samples, P["analysis_sheet"])
    pc.log(f"OK: {P['analysis_txt']}\n    {P['analysis_json'] if 'analysis_json' in P else P['analysis']}\n    {P['analysis_sheet']}")
    pc.log("\n" + P["analysis_txt"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
