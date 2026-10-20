#!/usr/bin/env python3
"""05b — Retoque facial avançado ("editor profissional"), determinístico, dirigido por polish.json.

Quatro ações, todas sutis por desenho e restritas à região do rosto:
  * nariz      — redução leve da ponta/asas por warp local tipo "pinch" (campo elíptico C1 centrado no
                 bulbo nasal, escala parametrizada; mesmos parâmetros em todos os quadros);
  * cabelo     — entradas/falhas na linha do cabelo: dentro da zona de cabelo (segmentação) o couro cabeludo
                 que aparece entre os fios existentes é escurecido com estrutura de mechas (ruído anisotrópico
                 preso ao rosto), densidade crescendo da borda para dentro (~5 mm), tom/croma da franja local,
                 grão e desfoque casados com o vídeo — nada é "desenhado" fora da máscara de cabelo;
  * barba      — preenchimento das falhas da barba: na zona geométrica da barba (landmarks), onde há
                 barba ao redor mas o pixel está claro, o tom baixa para o tom local da barba densa e o
                 detalhe (fios) é reforçado + clonado com pequeno deslocamento;
  * pele       — separação de frequências: tom (baixa freq.) e manchas (média freq. de luma e croma)
                 são uniformizados por convolução normalizada dentro da máscara de pele; o detalhe fino
                 (poros, fios) é devolvido inteiro. Olhos, sobrancelhas, lábios, cabelo e barba ficam fora.

Landmarks: MediaPipe Face Landmarker (478 pontos, `models/face_landmarker.task`), suavizados no tempo
(Savitzky-Golay simétrico, sem atraso). Máscaras de cabelo/pele: MediaPipe selfie multiclass
(`models/selfie_multiclass_256x256.tflite`) num recorte da cabeça, suavizadas no tempo (±2 quadros).

Nunca altera a timeline: mesma contagem de quadros; no modo completo com saída .mp4 o áudio da entrada é
copiado (stream copy). Faixa inferior opcional protegida (`protect_bottom_from`, fração da altura) para
legendas queimadas.

Shape-up opcional (`hairline_shape`/`beard_shape`): linha-alvo do cabelo pelos terços faciais com "M" suave (nunca
sobe a linha; desce no máx. `hairline_lower_max` fw), linha da bochecha (Bezier costeleta→canto do bigode, pele limpa
acima), contorno do pescoço (~0,12 fw abaixo da mandíbula, pele limpa abaixo), sombra mandibular discreta; fios
sintetizados por ruído anisotrópico definido em coordenadas do rosto (presos à cabeça → sem fervura temporal).

Bloco em polish.json:
  "retouch": {"enabled": true, "nose_tip_reduce": 0.15, "hairline": 0.6, "beard": 0.6, "skin_texture": 0.5,
              "hairline_shape": false, "beard_shape": false, "hairline_lower_max": 0.08, "hairline_recession": 0.04,
              "protect_bottom_from": 0.60, "landmark_smooth_frames": 7}

Uso:
  python scripts/05b_retouch.py work/<n>/polish/polish.json                      # beauty.mkv -> retouch.mkv
  python scripts/05b_retouch.py polish.json --in polished.mp4 --out out.mp4       # vídeo inteiro + áudio copiado
  python scripts/05b_retouch.py polish.json --in v.mp4 --preview 3.2 10 18 --out-dir prev/   # PNGs antes|depois
  python scripts/05b_retouch.py polish.json --in v.mp4 --clip 10 3 --out teste.mp4 # trecho curto com áudio
  python scripts/05b_retouch.py polish.json --in v.mp4 --suggest 4                 # instantes frontais/nítidos
  Sobrescritas: --nose 0.25 --hairline 0.8 --beard 0.8 --skin 0.5
"""
import argparse
import math
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402

os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

LM_MODEL = pc.SKILL_ROOT / "models" / "face_landmarker.task"
SEG_MODEL = pc.SKILL_ROOT / "models" / "selfie_multiclass_256x256.tflite"
LM_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
SEG_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/"
           "selfie_multiclass_256x256.tflite")

DET_WIDTH = 540          # largura do quadro usado no rastreamento (landmarks + segmentação)
MASK_SCALE = 0.25        # máscaras de segmentação guardadas a 1/4 da resolução
SEG_INPUT = 256

# índices do Face Mesh (MediaPipe)
LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
LEYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
REYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
LBROW = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
RBROW = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]
OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176,
        149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]
NOSE_BULB = [1, 2, 4, 5, 19, 45, 275, 48, 278, 64, 294, 98, 327, 94, 220, 440, 115, 344, 129, 358, 49, 279]
NOSE_WIDTH_PAIRS = [(129, 358), (48, 278), (64, 294)]
IDX_SUBNASALE, IDX_CHIN, IDX_BRIDGE, IDX_TIP = 2, 152, 168, 1
IDX_FACE_L, IDX_FACE_R = 234, 454

DEFAULT_RETOUCH = pc.DEFAULT_POLISH.get("retouch", {})


# ---------------------------------------------------------------- MediaPipe
def _mp():
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision
    except Exception as e:  # pragma: no cover
        pc.die(f"mediapipe não disponível ({e}). Instale com `pip install mediapipe` (CPU).")
    return mp, mpp, vision


def ensure_models():
    missing = [(p, u) for p, u in ((LM_MODEL, LM_URL), (SEG_MODEL, SEG_URL)) if not p.exists()]
    for p, u in missing:
        pc.log(f"baixando {p.name} ...")
        import urllib.request
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(u, p)
        except Exception as e:
            pc.die(f"não consegui baixar {p.name}: {e}\n  URL: {u}")


class Landmarker:
    """Face Landmarker (478 pontos) em modo VIDEO (rastreia entre quadros)."""

    def __init__(self):
        mp, mpp, vision = _mp()
        self.mp = mp
        self.lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=str(LM_MODEL)),
            running_mode=vision.RunningMode.VIDEO, num_faces=1,
            min_face_detection_confidence=0.5, min_face_presence_confidence=0.5, min_tracking_confidence=0.5))
        self.t = 0

    def detect(self, rgb):
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        self.t += 34
        r = self.lm.detect_for_video(img, self.t)
        if not r.face_landmarks:
            return None
        h, w = rgb.shape[:2]
        return np.array([[p.x * w, p.y * h] for p in r.face_landmarks[0]], dtype=np.float32)

    def close(self):
        try:
            self.lm.close()
        except Exception:
            pass


class Segmenter:
    """Selfie multiclass (0 fundo, 1 cabelo, 2 pele do corpo, 3 pele do rosto, 4 roupa, 5 outros)."""

    def __init__(self):
        mp, mpp, vision = _mp()
        self.mp = mp
        self.seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
            base_options=mpp.BaseOptions(model_asset_path=str(SEG_MODEL)),
            running_mode=vision.RunningMode.IMAGE, output_category_mask=False, output_confidence_masks=True))

    def segment(self, rgb256):
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb256))
        r = self.seg.segment(img)
        masks = [m.numpy_view() for m in r.confidence_masks]
        masks = [m[..., 0] if m.ndim == 3 else m for m in masks]
        return masks[1], masks[3]   # cabelo, pele do rosto

    def close(self):
        try:
            self.seg.close()
        except Exception:
            pass


# ---------------------------------------------------------------- passo 1: rastrear
def track(video, start, dur, n_frames, smooth_frames=7, progress=True):
    """Landmarks por quadro (30 fps, px do vídeo) suavizados + máscaras de cabelo/pele a 1/4 da resolução
    (uint8, por quadro). Retorna dict ou None se nenhum rosto."""
    import cv2
    ensure_models()
    lm, seg = Landmarker(), Segmenter()
    rd = pc.FrameReader(video, width=DET_WIDTH, fps=pc.FPS, start=start, dur=dur)
    sc = rd.src_w / rd.w
    mw, mh = int(round(rd.src_w * MASK_SCALE)), int(round(rd.src_h * MASK_SCALE))
    pts_all, ok, hair, skin = [], [], [], []
    t0 = time.time()
    for i, fr in enumerate(rd):
        if i >= n_frames:
            break
        rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
        p = lm.detect(rgb)
        hm = np.zeros((mh, mw), np.uint8)
        sm = np.zeros((mh, mw), np.uint8)
        if p is not None:
            # recorte quadrado da cabeça (2.4× a largura do rosto, centro um pouco acima do nariz)
            fwid = float(np.linalg.norm(p[IDX_FACE_R] - p[IDX_FACE_L]))
            cx, cy = p[IDX_BRIDGE]
            cy -= 0.25 * fwid
            half = 1.2 * fwid
            x0, y0 = int(max(0, cx - half)), int(max(0, cy - half))
            x1, y1 = int(min(rd.w, cx + half)), int(min(rd.h, cy + half))
            if x1 - x0 > 32 and y1 - y0 > 32:
                crop = cv2.resize(rgb[y0:y1, x0:x1], (SEG_INPUT, SEG_INPUT), interpolation=cv2.INTER_AREA)
                h_c, s_c = seg.segment(crop)
                bx0, by0 = int(round(x0 * sc * MASK_SCALE)), int(round(y0 * sc * MASK_SCALE))
                bx1, by1 = int(round(x1 * sc * MASK_SCALE)), int(round(y1 * sc * MASK_SCALE))
                bx1, by1 = min(bx1, mw), min(by1, mh)
                if bx1 > bx0 and by1 > by0:
                    hm[by0:by1, bx0:bx1] = np.clip(cv2.resize(h_c, (bx1 - bx0, by1 - by0)) * 255, 0, 255).astype(np.uint8)
                    sm[by0:by1, bx0:bx1] = np.clip(cv2.resize(s_c, (bx1 - bx0, by1 - by0)) * 255, 0, 255).astype(np.uint8)
            pts_all.append(p * sc)
            ok.append(True)
        else:
            pts_all.append(None)
            ok.append(False)
        hair.append(hm)
        skin.append(sm)
        if progress and (i + 1) % 150 == 0:
            pc.log(f"  rastreando {i + 1}/{n_frames} ({time.time() - t0:.0f} s)")
    rd.close()
    lm.close()
    seg.close()
    n = len(pts_all)
    if n == 0 or not any(ok):
        return None
    # suavização temporal dos landmarks (preenche lacunas e filtra jitter sem atraso)
    P = np.full((n, 478, 2), np.nan, np.float32)
    for i, p in enumerate(pts_all):
        if p is not None:
            P[i] = p
    flat = P.reshape(n, -1)
    for c in range(flat.shape[1]):
        v = pc.fill_gaps(flat[:, c])
        flat[:, c] = pc.smooth(v, smooth_frames) if n >= 5 else v
    P = flat.reshape(n, 478, 2)
    # confiança: a menos de 0,5 s de uma detecção real
    okarr = np.array(ok, bool)
    idx_ok = np.flatnonzero(okarr)
    dist = np.abs(np.arange(n)[:, None] - idx_ok[None, :]).min(axis=1)
    conf = dist <= pc.FPS // 2
    T = {"pts": P, "ok": okarr, "conf": conf, "hair": np.stack(hair), "skin": np.stack(skin),
         "detected_frac": float(okarr.mean())}
    T["hair_center_v"] = hair_center_series(T, smooth_frames)
    return T


def hair_center_series(T, smooth_frames=7):
    """Altura atual da linha do cabelo no centro da testa (v normalizado por fw, no referencial do rosto),
    medida na máscara de cabelo de cada quadro e suavizada no tempo (~1 s). NaN se não medível."""
    n, mh, mw = T["hair"].shape
    gx, gy = np.meshgrid(np.arange(mw, dtype=np.float32) / MASK_SCALE, np.arange(mh, dtype=np.float32) / MASK_SCALE)
    out = np.full(n, np.nan, np.float32)
    for i in range(n):
        L = T["pts"][i]
        fw = float(np.linalg.norm(L[IDX_FACE_R] - L[IDX_FACE_L]))
        if not np.isfinite(fw) or fw < 40:
            continue
        eL, eR = L[LEYE].mean(axis=0), L[REYE].mean(axis=0)
        th = math.atan2(float(eR[1] - eL[1]), float(eR[0] - eL[0]))
        ct, st = math.cos(th), math.sin(th)
        bx, by = L[IDX_BRIDGE]
        u = ((gx - bx) * ct + (gy - by) * st) / fw
        v = (-(gx - bx) * st + (gy - by) * ct) / fw
        bro = L[LBROW + RBROW]
        v_brow = float((-(bro[:, 0] - bx) * st + (bro[:, 1] - by) * ct).min() / fw)
        sel = (T["hair"][i] > 128) & (np.abs(u) < 0.15) & (v < v_brow - 0.03)
        if sel.sum() >= 20:
            out[i] = float(np.percentile(v[sel], 95))
    if np.all(np.isnan(out)):
        return out
    v = pc.fill_gaps(out)
    return pc.smooth(v, max(smooth_frames, 31)) if n >= 5 else v


def masks_at(T, i, radius=2):
    """Máscaras de cabelo/pele (float 0–1, 1/4 res) suavizadas no tempo (gaussiana ±radius quadros)."""
    n = T["hair"].shape[0]
    ws, hs, ss = 0.0, None, None
    for d in range(-radius, radius + 1):
        j = min(max(i + d, 0), n - 1)
        w = math.exp(-0.5 * (d / max(radius / 1.5, 0.5)) ** 2)
        h = T["hair"][j].astype(np.float32) * w
        s = T["skin"][j].astype(np.float32) * w
        hs = h if hs is None else hs + h
        ss = s if ss is None else ss + s
        ws += w
    return hs / (255.0 * ws), ss / (255.0 * ws)


# ---------------------------------------------------------------- utilidades de imagem
def gblur(img, sigma):
    """GaussianBlur; sigmas grandes (> 4 px) vão pela versão reduzida (mesmo resultado visual, ~10× mais rápido)."""
    import cv2
    if sigma <= 0:
        return img
    if sigma > 4.0:
        return wide_blur(img, sigma)
    return cv2.GaussianBlur(img, (0, 0), sigma)


def wide_blur(img, sigma, scale=0.25):
    """GaussianBlur de sigma grande via redução (sigma em px da imagem original)."""
    import cv2
    h, w = img.shape[:2]
    if sigma * scale < 1.0:
        return cv2.GaussianBlur(img, (0, 0), sigma)
    small = cv2.resize(img, (max(2, int(w * scale)), max(2, int(h * scale))), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma * scale)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def nblur(img, mask, sigma):
    """Convolução normalizada: média local ponderada pela máscara (não puxa cor de fora da máscara)."""
    num = wide_blur(img * mask, sigma)
    den = wide_blur(mask, sigma)
    return num / np.maximum(den, 1e-3)


def poly_mask(shape, pts, dilate_px=0.0, feather=0.0, scale=0.25):
    """Polígono preenchido (0–1) com dilatação e borda suave, calculado em resolução reduzida (máscaras são
    suaves por natureza; dilatar com kernel grande em resolução cheia custa ~40 ms por máscara)."""
    import cv2
    h, w = shape[:2]
    sh, sw = max(2, int(round(h * scale))), max(2, int(round(w * scale)))
    m = np.zeros((sh, sw), np.float32)
    p = np.round(np.asarray(pts) * scale).astype(np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(m, [p], 1.0)
    if dilate_px * scale > 0.5:
        k = int(dilate_px * scale) * 2 + 1
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if feather * scale > 0.3:
        m = cv2.GaussianBlur(m, (0, 0), feather * scale)
    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(m, 0, 1)


def smoothstep(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------- síntese de fios (ancorada ao rosto)
NGRID = 600            # células por largura de rosto (fw) — ~0,8 px num rosto de 460 px
NU, NV = 0.9, 1.3      # extensão da grade em coordenadas normalizadas do rosto (u, v em fw)
_NOISE = {}


def fiber_noise(kind):
    """Ruído anisotrópico (estrias ~verticais) numa grade em coordenadas do rosto, gerado uma vez (semente fixa):
    amostrado por quadro com remap → os "fios" ficam presos ao rosto e não fervem. z-score (média 0, desvio 1)."""
    if kind in _NOISE:
        return _NOISE[kind]
    import cv2
    h, w = int(2 * NV * NGRID), int(2 * NU * NGRID)
    seed, su, sv = {"hair": (11, 0.9, 12.0), "beard": (23, 0.6, 4.0), "density": (31, 20.0, 20.0)}[kind]
    rng = np.random.default_rng(seed)
    n = rng.standard_normal((h, w)).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), sigmaX=su, sigmaY=sv)
    n2 = cv2.GaussianBlur(rng.standard_normal((h, w)).astype(np.float32), (0, 0), sigmaX=1.6, sigmaY=1.6)
    n = n / (n.std() + 1e-6) + (0.0 if kind == "density" else 0.35) * n2 / (n2.std() + 1e-6)
    n = (n / (n.std() + 1e-6)).astype(np.float32)
    _NOISE[kind] = n
    return n


def edge_jitter(kind):
    """Irregularidade 1-D da borda (em fw) ao longo de u: fina (fios isolados) + larga (ondulação)."""
    key = "jit_" + kind
    if key in _NOISE:
        return _NOISE[key]
    import cv2
    w = int(2 * NU * NGRID)
    rng = np.random.default_rng({"hair": 5, "beard": 17}[kind])
    a = cv2.GaussianBlur(rng.standard_normal((1, w)).astype(np.float32), (0, 0), sigmaX=2.5, sigmaY=0.1)[0]
    b = cv2.GaussianBlur(rng.standard_normal((1, w)).astype(np.float32), (0, 0), sigmaX=18, sigmaY=0.1)[0]
    j = 0.006 * a / (a.std() + 1e-6) + 0.006 * b / (b.std() + 1e-6)
    _NOISE[key] = j.astype(np.float32)
    return _NOISE[key]


def sample_face_grid(grid2d, Un, Vn):
    """Amostra uma grade em coordenadas do rosto (u, v normalizados por fw) nos pixels do ROI."""
    import cv2
    mx = ((Un + NU) * NGRID).astype(np.float32)
    my = ((Vn + NV) * NGRID).astype(np.float32)
    return cv2.remap(grid2d, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def sample_u_curve(curve1d, Un):
    """Avalia uma função de u (array na grade [-NU, NU]) em cada pixel."""
    ug = (np.arange(curve1d.shape[0]) / NGRID - NU).astype(np.float32)
    return np.interp(Un.ravel(), ug, curve1d).reshape(Un.shape).astype(np.float32)


def bezier_v_of_u(p0, c, p1):
    """Curva quadrática (u,v) → função v(u) na grade de u (fora do intervalo: valor da ponta mais próxima)."""
    t = np.linspace(0, 1, 96, dtype=np.float32)[:, None]
    pts = (1 - t) ** 2 * np.array(p0) + 2 * (1 - t) * t * np.array(c) + t ** 2 * np.array(p1)
    order = np.argsort(pts[:, 0])
    ug = np.arange(int(2 * NU * NGRID)) / NGRID - NU
    return np.interp(ug, pts[order, 0], pts[order, 1]).astype(np.float32)


def percentile_masked(v, m, q, default):
    sel = v[m > 0.5]
    return float(np.percentile(sel, q)) if sel.size >= 50 else default


# ---------------------------------------------------------------- passo 2: retoque de um quadro
def retouch_frame(frame, L, hair_lo, skin_lo, R, protect_y=None, debug=None, extra=None):
    """frame BGR uint8; L (478,2) px; hair_lo/skin_lo máscaras 0–1 a MASK_SCALE; R = bloco retouch.
    debug: dict opcional que recebe as máscaras intermediárias (desenvolvimento)."""
    import cv2
    H, W = frame.shape[:2]
    nose_s = float(R.get("nose_tip_reduce") or 0)
    k_hair = float(R.get("hairline") or 0)
    k_beard = float(R.get("beard") or 0)
    k_skin = float(R.get("skin_texture") or 0)
    if max(nose_s, k_hair, k_beard, k_skin) <= 0:
        return frame

    fw = float(np.linalg.norm(L[IDX_FACE_R] - L[IDX_FACE_L]))
    if not np.isfinite(fw) or fw < 40:
        return frame
    eyeL, eyeR = L[LEYE].mean(axis=0), L[REYE].mean(axis=0)
    theta = math.atan2(float(eyeR[1] - eyeL[1]), float(eyeR[0] - eyeL[0]))
    ct, st = math.cos(theta), math.sin(theta)
    bridge = L[IDX_BRIDGE]

    def fcoord(p):
        d = p - bridge
        return (d[..., 0] * ct + d[..., 1] * st, -d[..., 0] * st + d[..., 1] * ct)

    # ROI: landmarks + margem (cabelo acima)
    xs, ys = L[:, 0], L[:, 1]
    x0, x1 = int(max(0, xs.min() - 0.40 * fw)), int(min(W, xs.max() + 0.40 * fw))
    y0, y1 = int(max(0, ys.min() - 0.75 * fw)), int(min(H, ys.max() + 0.20 * fw))
    if protect_y is not None:
        y1 = min(y1, int(protect_y))
    if x1 - x0 < 32 or y1 - y0 < 32:
        return frame
    roi = frame[y0:y1, x0:x1]
    rh, rw = roi.shape[:2]
    Lr = L - np.array([x0, y0], np.float32)
    ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb).astype(np.float32)   # uint8 -> croma centrado em 128
    Y, Cr, Cb = ycc[..., 0].copy(), ycc[..., 1].copy(), ycc[..., 2].copy()
    shape = (rh, rw)
    touched = np.zeros(shape, bool)      # pixels com algum retoque; os demais voltam bit-a-bit iguais

    # coordenadas no referencial do rosto (u ao longo dos olhos, v para baixo)
    gx, gy = np.meshgrid(np.arange(rw, dtype=np.float32) + x0, np.arange(rh, dtype=np.float32) + y0)
    U, V = fcoord(np.stack([gx, gy], -1))
    v_brow_top = float(min(fcoord(L[LBROW])[1].min(), fcoord(L[RBROW])[1].min()))
    v_subnasale = float(fcoord(L[IDX_SUBNASALE])[1])
    Un, Vn = U / fw, V / fw                                   # coordenadas normalizadas do rosto
    Lu, Lv = fcoord(L)
    Lu, Lv = Lu / fw, Lv / fw
    shape_hair = bool(R.get("hairline_shape")) and k_hair > 0
    shape_beard = bool(R.get("beard_shape")) and k_beard > 0
    extra = extra or {}

    # máscaras de segmentação (1/4 -> ROI)
    def up(m):
        full = cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR) if m.shape != (H, W) else m
        return full[y0:y1, x0:x1]
    Hm = np.clip(up(hair_lo), 0, 1)
    Sm = np.clip(up(skin_lo), 0, 1)

    # máscaras geométricas
    oval = poly_mask(shape, Lr[OVAL], feather=0.02 * fw)
    eyes = np.clip(poly_mask(shape, Lr[LEYE], 0.045 * fw, 0.02 * fw) + poly_mask(shape, Lr[REYE], 0.045 * fw, 0.02 * fw), 0, 1)
    brows = np.clip(poly_mask(shape, Lr[LBROW], 0.03 * fw, 0.015 * fw) + poly_mask(shape, Lr[RBROW], 0.03 * fw, 0.015 * fw), 0, 1)
    lips = poly_mask(shape, Lr[LIPS], 0.035 * fw, 0.02 * fw)
    nose_c = Lr[NOSE_BULB].mean(axis=0)
    nose_w = float(np.mean([np.linalg.norm(Lr[a] - Lr[b]) for a, b in NOSE_WIDTH_PAIRS]))
    nose_mask = np.zeros(shape, np.float32)
    cv2.ellipse(nose_mask, (int(nose_c[0]), int(nose_c[1])), (int(0.85 * nose_w), int(0.75 * nose_w)),
                math.degrees(theta), 0, 360, 1.0, -1)
    nose_mask = gblur(nose_mask, 0.02 * fw)

    Y3 = gblur(Y, 3.0)
    # zona geométrica da barba: dentro do oval, abaixo da base do nariz, fora de lábios e nariz
    beard_zone = oval * smoothstep((V - v_subnasale + 0.02 * fw) / (0.08 * fw)) * (1 - lips) * (1 - nose_mask)
    below_line = jaw_band = clean_cheek = neck_clean = None
    if shape_beard:
        # --- "shape-up" de barbeiro em coordenadas do rosto: linha da bochecha (Bezier do topo da costeleta
        # ao canto do bigode, levemente curva), contorno do pescoço ~0,12 fw abaixo da mandíbula, borda com
        # transição fina + irregularidade de fios (jitter 1-D + estrias ancoradas ao rosto).
        v2n = float(Lv[IDX_SUBNASALE])
        Tb = sample_face_grid(fiber_noise("beard"), Un, Vn)
        jb = sample_u_curve(edge_jitter("beard"), Un)
        ug_n = int(2 * NU * NGRID)
        vline = np.full(ug_n, v2n + 0.015, np.float32)
        ug = np.arange(ug_n) / NGRID - NU
        for i_side, i_mouth, sgn in ((IDX_FACE_L, 61, -1), (IDX_FACE_R, 291, +1)):
            # topo da costeleta ~0,08 fw acima da base do nariz, na lateral do rosto; fim no canto do bigode
            p0 = (float(Lu[i_side]), v2n - 0.08)
            p1 = (float(Lu[i_mouth]) + sgn * 0.03, v2n + 0.03)
            c = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 + 0.03)
            bz = bezier_v_of_u(p0, c, p1)
            lo, hi = sorted([p0[0], p1[0]])
            sel = (ug >= lo) & (ug <= hi)
            vline[sel] = bz[sel]
            beyond = (ug < lo) if sgn < 0 else (ug > hi)
            vline[beyond] = p0[1]
        vline_px = sample_u_curve(vline, Un)
        below_line = smoothstep((Vn - vline_px - jb - 0.012 * Tb) / 0.014)
        # oval estendido para baixo (pescoço) e faixa de sombra mandibular
        v_mouth = float(Lv[17])
        ext = Lr[OVAL].copy()
        low_pts = Lv[OVAL] > v_mouth - 0.05
        ext[low_pts] += np.array([-st, ct], np.float32) * (0.12 * fw)
        oval_neck = poly_mask(shape, ext, feather=0.015 * fw)
        oval_hard = poly_mask(shape, Lr[OVAL], feather=0.008 * fw)
        oval_jaw = poly_mask(shape, Lr[OVAL], dilate_px=0.04 * fw, feather=0.008 * fw)
        jaw_band = np.clip(oval_jaw - oval_hard, 0, 1) * smoothstep((Vn - v_mouth) / 0.05)
        jaw_band = gblur(jaw_band, 0.012 * fw)
        neck_clean = np.clip(1 - oval_neck, 0, 1) * smoothstep((Vn - v_mouth - 0.1) / 0.05)
        beard_zone = oval_neck * below_line * (1 - lips) * (1 - nose_mask)
        clean_cheek = oval * (1 - below_line) * smoothstep((vline_px + 0.12 - Vn) / 0.04) * (1 - nose_mask) * (1 - lips) \
            * smoothstep((Vn - float(Lv[LEYE].max()) - 0.06) / 0.04) * smoothstep((np.abs(Un) - 0.12) / 0.05)
    # pele "limpa" (sem cabelo, olhos, sobrancelhas, lábios)
    skin_core = Sm * oval * (1 - eyes) * (1 - brows) * (1 - lips) * (1 - Hm)
    y_skin_ref = percentile_masked(Y3, skin_core * (1 - beard_zone), 50, 150.0)
    y_beard_ref = percentile_masked(Y3, beard_zone, 15, y_skin_ref - 60)
    span = max(y_skin_ref - y_beard_ref, 25.0)
    beardness = np.clip((y_skin_ref - 12 - Y3) / span, 0, 1) * beard_zone      # 1 = barba escura
    beard_density = wide_blur(beardness, 0.05 * fw)                              # quanta barba há ao redor
    beard_soft = np.clip(wide_blur(beardness, 0.02 * fw) * 1.5, 0, 1)
    if debug is not None:
        debug.update(roi=(x0, y0, x1, y1), Hm=Hm, Sm=Sm, oval=oval, beard_zone=beard_zone, beardness=beardness,
                     beard_density=beard_density, skin_core=skin_core)

    # ---------------------------------------------------------------- pele
    if k_skin > 0:
        S = skin_core * (1 - beard_soft) * (1 - np.clip(beard_density * 2.5 - 0.3, 0, 1) * beard_zone)
        S = np.clip(gblur(S, 0.012 * fw), 0, 1)
        Y_low = nblur(Y, S, 0.03 * fw)
        Y_mid = gblur(Y, 1.8)
        high = Y - Y_mid
        mid = Y_mid - Y_low
        Y_new = Y_low + mid * (1 - 0.65 * k_skin) + high
        Cr_new = nblur(Cr, S, 0.035 * fw)
        Cb_new = nblur(Cb, S, 0.035 * fw)
        a = S * min(1.0, 1.2 * k_skin)
        touched |= a > 1e-3
        Y = Y + a * (Y_new - Y)
        Cr = Cr + a * 0.8 * (Cr_new - Cr)
        Cb = Cb + a * 0.8 * (Cb_new - Cb)

    # ---------------------------------------------------------------- barba
    if k_beard > 0 and y_skin_ref - y_beard_ref > 20:
        near = smoothstep(beard_density * 4.0 - 0.25)                              # há barba na vizinhança
        light = np.clip(1.0 - beardness / np.maximum(beard_zone, 1e-3), 0, 1)     # pixel claro (falha)
        if shape_beard:                                                            # a zona define onde há barba:
            dens = 0.6 + 0.3 * smoothstep((Vn - vline_px) / 0.08)                  # densidade cresce da linha p/ baixo
            near = np.maximum(near, dens * below_line)
        w = beard_zone * near * smoothstep(light)
        w = np.clip(gblur(w, 0.012 * fw if not shape_beard else 0.006 * fw), 0, 1)
        if shape_beard:
            w = w * (1 - 0.5 * clean_cheek)
        if debug is not None:
            debug["beard_w"] = w
        a = w * k_beard
        touched |= a > 1e-3
        Y_low3 = gblur(Y, 3.0)
        det = Y - Y_low3
        wb = beardness * beard_zone
        y_dense_local = nblur(Y_low3, wb, 0.07 * fw)
        has = np.clip(wide_blur(wb, 0.07 * fw) * 20, 0, 1)
        y_dense_local = has * y_dense_local + (1 - has) * y_beard_ref
        target_low = Y_low3 + (y_dense_local - Y_low3) * (0.32 if shape_beard else 0.55)
        # detalhe clonado: cópia deslocada em direção ao queixo (fios "extras")
        d = Lr[IDX_CHIN] - Lr[IDX_SUBNASALE]
        d = d / max(np.linalg.norm(d), 1e-3) * (0.035 * fw)
        M = np.float32([[1, 0, -d[0]], [0, 1, -d[1]]])
        det_clone = cv2.warpAffine(det, M, (rw, rh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        det_new = det * (1 + (0.8 if shape_beard else 0.45) * a) + det_clone * (0.6 * a)
        Y = Y_low3 + a * (target_low - Y_low3) + det_new      # onde a = 0: Y_low3 + det = Y (inalterado)
        if shape_beard:
            Y = Y - a * np.clip(-Tb - 0.25, 0, 2.5) * 28.0      # fios = traços ESCUROS esparsos ancorados ao rosto (pele entre eles fica)
        selb = wb > 0.6
        if selb.sum() > 100:
            cr_b, cb_b = float(np.median(Cr[selb])), float(np.median(Cb[selb]))
            kc = 0.3 if shape_beard else 0.5
            Cr = Cr + a * kc * (cr_b - Cr)
            Cb = Cb + a * kc * (cb_b - Cb)
        if shape_beard:
            # pele limpa acima da linha da bochecha e abaixo do contorno do pescoço: só clareia os pontos escuros
            # (pelos isolados), preservando a textura clara da pele
            Yl = gblur(Y, 2.5)
            cl_w = np.clip(clean_cheek + neck_clean, 0, 1) * k_beard
            touched |= (cl_w > 1e-3) | (jaw_band * k_beard > 1e-3)
            Y = Y + cl_w * 0.75 * np.clip(Yl - Y, 0, None)
            Y = Y + cl_w * 0.35 * (gblur(Y, 1.5) - Y)
            # sombra mandibular discreta (definição da mandíbula)
            Y = Y * (1 - 0.10 * k_beard * jaw_band)
        if debug is not None and shape_beard:
            debug.update(below_line=below_line, clean_cheek=clean_cheek, jaw_band=jaw_band, neck_clean=neck_clean)

    # ---------------------------------------------------------------- linha do cabelo
    # v3 ("indetectável"): nada de "capacete". Só escurece o couro cabeludo que aparece ENTRE os fios existentes
    # (dentro da máscara de cabelo) com estrutura de mechas, densidade crescendo para dentro (transição 3–6 mm),
    # tom/croma da franja local, grão e desfoque casados com o vídeo. A linha-alvo geométrica (hairline_shape) é
    # opcional e também passa por essa mesma camada.
    if k_hair > 0:
        Hs = wide_blur(Hm, 0.015 * fw)
        head_top = smoothstep((v_brow_top - 0.07 * fw - V) / (0.05 * fw))
        core = (Hm > 0.85).astype(np.float32)
        core = cv2.erode(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        y_hair_ref = percentile_masked(Y3, core * head_top, 35, 60.0)
        if core.sum() > 200 and y_skin_ref - y_hair_ref > 30:
            frame_seed = int(extra.get("frame_index", 0) or 0)
            Th = sample_face_grid(fiber_noise("hair"), Un, Vn)             # mechas (presas ao rosto)
            Td = sample_face_grid(fiber_noise("density"), Un, Vn)          # variação lenta de densidade
            # zona: dentro da máscara de cabelo (sem expandir sobre a testa) + gradiente de densidade da borda p/ dentro
            inside = (Hs > 0.5).astype(np.uint8)
            dist_in = cv2.distanceTransform(inside, cv2.DIST_L2, 5)
            grad = smoothstep(dist_in / (0.045 * fw))                       # 0 na borda → 1 a ~0,045 fw (≈5 mm)
            zone = smoothstep((Hs - 0.35) / 0.3) * head_top * (1 - brows) * (1 - eyes)
            if shape_hair and extra.get("hair_center_v") is not None and np.isfinite(extra["hair_center_v"]):
                v_gl, v_sn = float(Lv[9]), float(Lv[IDX_SUBNASALE])
                v_t = v_gl - (v_sn - v_gl)
                v_hc = float(extra["hair_center_v"])
                lower_max = float(R.get("hairline_lower_max", 0.08) or 0.08)
                v_tc = min(max(v_t, v_hc), v_hc + lower_max)
                rec = float(R.get("hairline_recession", 0.04) or 0.04)
                absu = np.abs(Un)
                v_target = v_tc - rec * smoothstep((absu - 0.21) / 0.04)
                jh = sample_u_curve(edge_jitter("hair"), Un)
                geo = smoothstep((v_target + jh + 0.012 * Th - Vn) / 0.02) * smoothstep((0.50 - absu) / 0.05)
                geo = geo * smoothstep((Vn - (v_target - 0.45)) / 0.05) * head_top * (1 - brows)
                geo = geo * np.clip(wide_blur(np.maximum(Hm, oval), 0.03 * fw) * 1.6, 0, 1)
                gdist = smoothstep((v_target + jh - Vn) / 0.045)              # gradiente geométrico equivalente
                zone = np.maximum(zone, geo)
                grad = np.maximum(grad, geo * gdist)
                if debug is not None:
                    debug.update(hair_w_geo=geo, hair_target=(v_t, v_hc, v_tc))
            sparse = np.clip((Y3 - y_hair_ref - 10) / 50.0, 0, 1)            # couro cabeludo/pele aparecendo
            dens_var = np.clip(0.75 + 0.25 * Td, 0.4, 1.0)                    # irregularidade lenta (não uniforme)
            w = zone * sparse * (0.25 + 0.75 * grad) * dens_var
            w = np.clip(gblur(w, 0.008 * fw), 0, 1)
            if debug is not None:
                debug.update(hair_zone=zone, hair_sparse=sparse, hair_w=w, hair_core=core, hair_grad=grad)
            if w.max() > 0.05:
                # tom/croma da FRANJA local (cabelo escuro mais próximo), não do núcleo denso
                dark = core * np.clip((y_hair_ref + 25 - Y3) / 25.0, 0, 1)
                y_loc = nblur(Y3, dark, 0.06 * fw)
                cr_loc = nblur(Cr, dark, 0.06 * fw)
                cb_loc = nblur(Cb, dark, 0.06 * fw)
                has = np.clip(wide_blur(dark, 0.06 * fw) * 30, 0, 1)
                sel = core > 0.5
                cr_h = float(np.median(Cr[sel])) if sel.sum() > 50 else 128.0
                cb_h = float(np.median(Cb[sel])) if sel.sum() > 50 else 128.0
                y_loc = has * y_loc + (1 - has) * y_hair_ref
                cr_loc = has * cr_loc + (1 - has) * cr_h
                cb_loc = has * cb_loc + (1 - has) * cb_h
                # estrutura de mechas: onde há mecha escurece até o tom da franja; entre mechas só sombra leve
                m = smoothstep((Th - 0.1) / 0.8)
                cover = 0.30 + 0.70 * m
                Y9 = gblur(Y, 0.02 * fw)
                fill_Y = Y - (Y9 - y_loc) * cover                              # mantém o detalhe próprio (fios reais)
                fill_Cr = Cr + (cr_loc - Cr) * cover
                fill_Cb = Cb + (cb_loc - Cb) * cover
                # casar nitidez e grão com o vídeo: leve desfoque + ruído com o desvio medido na pele
                hp = Y - gblur(Y, 1.2)
                sig = float(np.std(hp[skin_core > 0.6])) if (skin_core > 0.6).sum() > 500 else 1.5
                rng = np.random.default_rng(1000 + frame_seed)
                grain = rng.standard_normal(fill_Y.shape).astype(np.float32) * sig * 0.9
                fill_Y = gblur(fill_Y, 0.9) + grain
                a = w * k_hair
                touched |= a > 1e-3
                Y = Y + a * (fill_Y - Y)
                Cr = Cr + a * (fill_Cr - Cr)
                Cb = Cb + a * (fill_Cb - Cb)

    ycc2 = np.stack([np.clip(Y, 0, 255), np.clip(Cr, 0, 255), np.clip(Cb, 0, 255)], -1)
    out = cv2.cvtColor(np.round(ycc2).astype(np.uint8), cv2.COLOR_YCrCb2BGR).astype(np.float32)
    out[~touched] = roi[~touched]                       # sem retoque = pixel original (evita ruído de conversão)

    # ---------------------------------------------------------------- nariz (warp "pinch" elíptico)
    if nose_s > 0:
        a_ax, b_ax = 0.95 * nose_w, 0.80 * nose_w
        cxn, cyn = float(nose_c[0]), float(nose_c[1])
        half = int(max(a_ax, b_ax) * 1.3) + 2
        sx0, sy0 = max(0, int(cxn) - half), max(0, int(cyn) - half)
        sx1, sy1 = min(rw, int(cxn) + half), min(rh, int(cyn) + half)
        if sx1 - sx0 > 8 and sy1 - sy0 > 8:
            X, Yg = np.meshgrid(np.arange(sx0, sx1, dtype=np.float32), np.arange(sy0, sy1, dtype=np.float32))
            dx, dy = X - cxn, Yg - cyn
            u = dx * ct + dy * st
            v = -dx * st + dy * ct
            r2 = (u / a_ax) ** 2 + (v / b_ax) ** 2
            wgt = np.where(r2 < 1, (1 - r2) ** 2, 0).astype(np.float32)
            su = 1 + nose_s * wgt
            sv = 1 + 0.6 * nose_s * wgt
            u2, v2 = u * su, v * sv
            mx = (cxn + u2 * ct - v2 * st - sx0).astype(np.float32)
            my = (cyn + u2 * st + v2 * ct - sy0).astype(np.float32)
            sub = out[sy0:sy1, sx0:sx1]
            out[sy0:sy1, sx0:sx1] = cv2.remap(sub, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)

    res = frame.copy()
    res[y0:y1, x0:x1] = np.clip(out, 0, 255).astype(np.uint8)
    return res


# ---------------------------------------------------------------- helpers de E/S
def protect_row(cfg_r, H):
    p = cfg_r.get("protect_bottom_from")
    if p is None:
        return None
    p = float(p)
    return int(H * p) if p <= 1.0 else int(p)


def apply_protect(res, orig, protect_y, feather=24):
    """Devolve o quadro com a faixa inferior (>= protect_y) idêntica ao original, transição suave."""
    if protect_y is None:
        return res
    H = res.shape[0]
    if protect_y >= H:
        return res
    y_a = max(0, protect_y - feather)
    out = res.copy()
    out[protect_y:] = orig[protect_y:]
    if protect_y > y_a:
        t = np.linspace(0, 1, protect_y - y_a, dtype=np.float32)[:, None, None]
        out[y_a:protect_y] = np.clip(np.round(res[y_a:protect_y] * (1 - t) + orig[y_a:protect_y] * t), 0, 255).astype(np.uint8)
    return out


def face_crop_box(L, W, H, protect_y=None):
    xs, ys = L[:, 0], L[:, 1]
    fw = float(np.linalg.norm(L[IDX_FACE_R] - L[IDX_FACE_L]))
    x0, x1 = int(max(0, xs.min() - 0.25 * fw)), int(min(W, xs.max() + 0.25 * fw))
    y0, y1 = int(max(0, ys.min() - 0.70 * fw)), int(min(H, ys.max() + 0.15 * fw))
    if protect_y is not None:
        y1 = min(y1, protect_y)
    return x0, y0, x1, y1


def label(img, text):
    import cv2
    out = img.copy()
    cv2.rectangle(out, (0, 0), (len(text) * 22 + 20, 48), (0, 0, 0), -1)
    cv2.putText(out, text, (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def suggest_instants(video, n_pick, start=0.0):
    """Instantes frontais, nítidos e bem distribuídos (amostra a 2 fps)."""
    import cv2
    ensure_models()
    lm = Landmarker()
    rd = pc.FrameReader(video, width=DET_WIDTH, fps=2)
    rows = []
    for i, fr in enumerate(rd):
        p = lm.detect(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
        if p is None:
            continue
        fw = float(np.linalg.norm(p[IDX_FACE_R] - p[IDX_FACE_L]))
        yaw = (abs(p[IDX_TIP, 0] - p[IDX_FACE_L, 0]) - abs(p[IDX_FACE_R, 0] - p[IDX_TIP, 0])) / max(fw, 1)
        roll = math.degrees(math.atan2(p[REYE].mean(0)[1] - p[LEYE].mean(0)[1], p[REYE].mean(0)[0] - p[LEYE].mean(0)[0]))
        x0, y0 = int(max(0, p[:, 0].min())), int(max(0, p[:, 1].min()))
        x1, y1 = int(min(rd.w, p[:, 0].max())), int(min(rd.h, p[:, 1].max()))
        g = cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(g, cv2.CV_32F).var()) if g.size else 0.0
        # boca pouco aberta (lábios próximos) ajuda a ver a barba
        mouth_open = float(np.linalg.norm(p[13] - p[14])) / max(fw, 1)
        rows.append((i / 2.0 + start, abs(yaw), abs(roll), sharp, mouth_open))
    rd.close()
    lm.close()
    if not rows:
        return []
    rows = np.array(rows)
    score = -3.0 * rows[:, 1] - 0.02 * rows[:, 2] + 0.6 * np.log1p(rows[:, 3] / max(np.median(rows[:, 3]), 1)) - 2.0 * rows[:, 4]
    T = rows[-1, 0] - rows[0, 0]
    picks = []
    for k in range(n_pick):
        lo, hi = rows[0, 0] + T * k / n_pick, rows[0, 0] + T * (k + 1) / n_pick
        sel = np.flatnonzero((rows[:, 0] >= lo) & (rows[:, 0] < hi))
        if sel.size:
            best = sel[np.argmax(score[sel])]
            picks.append(float(rows[best, 0]))
    return picks


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Retoque facial avançado (nariz, cabelo, barba, pele) — MediaPipe + OpenCV.")
    ap.add_argument("polish")
    ap.add_argument("--workdir")
    ap.add_argument("--in", dest="inp", help="entrada (padrão: beauty.mkv)")
    ap.add_argument("--out", help="saída (padrão: retouch.mkv; .mp4 = vídeo + áudio copiado da entrada)")
    ap.add_argument("--preview", type=float, nargs="+", metavar="S", help="instantes (s): PNGs antes|depois")
    ap.add_argument("--out-dir", help="pasta das prévias (padrão: <workdir>/retouch_preview)")
    ap.add_argument("--clip", type=float, nargs=2, metavar=("INICIO", "DUR"), help="processa só um trecho (s)")
    ap.add_argument("--suggest", type=int, metavar="N", help="sugere N instantes frontais/nítidos e sai")
    ap.add_argument("--nose", type=float, help="sobrescreve nose_tip_reduce")
    ap.add_argument("--hairline", type=float)
    ap.add_argument("--beard", type=float)
    ap.add_argument("--skin", type=float, help="sobrescreve skin_texture")
    ap.add_argument("--shape", choices=["on", "off"], help="sobrescreve hairline_shape/beard_shape")
    ap.add_argument("--crf", type=int, default=12)
    ap.add_argument("--force", action="store_true", help="roda mesmo com retouch.enabled=false")
    args = ap.parse_args()

    cfg_path = Path(args.polish).resolve()
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    R = dict(cfg.get("retouch") or {})
    for k, v in (("nose_tip_reduce", args.nose), ("hairline", args.hairline), ("beard", args.beard), ("skin_texture", args.skin)):
        if v is not None:
            R[k] = v
    if args.shape:
        R["hairline_shape"] = R["beard_shape"] = (args.shape == "on")
    inp = Path(args.inp).resolve() if args.inp else P["beauty"]
    if not inp.exists() and (args.preview is not None or args.suggest):
        inp = pc.polish_source(cfg, cfg_path)
    if not inp.exists():
        pc.die(f"entrada não encontrada: {inp}")
    info = pc.probe(inp)
    W, H = info["width"], info["height"]
    protect_y = protect_row(R, H)
    smooth_n = int(R.get("landmark_smooth_frames") or 7)

    if args.suggest:
        picks = suggest_instants(inp, args.suggest)
        pc.log("instantes sugeridos (s): " + " ".join(f"{t:.2f}" for t in picks))
        return

    # ---------------- prévias
    if args.preview is not None:
        import cv2
        out_dir = Path(args.out_dir) if args.out_dir else workdir / "retouch_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        pc.log(f"retoque: nariz {R.get('nose_tip_reduce')}, cabelo {R.get('hairline')}, barba {R.get('beard')}, "
               f"pele {R.get('skin_texture')} (MediaPipe Face Landmarker)")
        for t in args.preview:
            win = 0.5
            start = max(0.0, t - win)
            dur = 2 * win + 1.0 / pc.FPS
            n = int(round(dur * pc.FPS))
            T = track(inp, start, dur, n, smooth_n, progress=False)
            if T is None:
                pc.log(f"  {t:.2f}s: sem rosto — pulado")
                continue
            i = int(np.clip(round((t - start) * pc.FPS), 0, T["pts"].shape[0] - 1))
            rd = pc.FrameReader(inp, start=start + i / pc.FPS, dur=0.05, fps=None)
            fr = next(iter(rd), None)
            rd.close()
            if fr is None:
                pc.log(f"  {t:.2f}s: fora do vídeo")
                continue
            hl, sl = masks_at(T, i)
            ex = {"hair_center_v": float(T["hair_center_v"][i]), "frame_index": int(round(t * pc.FPS))}
            res = apply_protect(retouch_frame(fr, T["pts"][i], hl, sl, R, protect_y, extra=ex), fr, protect_y)
            tag = f"t{t:06.2f}s"
            cv2.imwrite(str(out_dir / f"{tag}_full.png"), np.hstack([label(fr, "ANTES"), label(res, "DEPOIS")]))
            bx0, by0, bx1, by1 = face_crop_box(T["pts"][i], W, H, protect_y)
            ca, cb = fr[by0:by1, bx0:bx1], res[by0:by1, bx0:bx1]
            sc = max(1.0, 1400.0 / max(1, by1 - by0))
            ca = cv2.resize(ca, None, fx=sc, fy=sc, interpolation=cv2.INTER_LANCZOS4)
            cb = cv2.resize(cb, None, fx=sc, fy=sc, interpolation=cv2.INTER_LANCZOS4)
            cv2.imwrite(str(out_dir / f"{tag}_crop.png"), np.hstack([label(ca, "ANTES"), label(cb, "DEPOIS")]))
            pc.log(f"  OK {tag}: {out_dir / (tag + '_full.png')} + _crop.png")
        return

    if not R.get("enabled") and not args.force and args.clip is None:
        pc.log("retouch.enabled = false — copiando sem retoque")
        out = Path(args.out) if args.out else P["retouch"]
        shutil.copyfile(inp, out)
        pc.save_json(workdir / "retouch.json", {"applied": False})
        return

    # ---------------- vídeo (inteiro ou trecho)
    start, dur = (args.clip if args.clip else (0.0, None))
    src_dur = info["duration"] or 0.0
    if dur is None:
        dur = src_dur
    else:
        dur = min(dur, max(0.0, src_dur - start))
    n_frames = int(round(dur * pc.FPS))
    out = Path(args.out).resolve() if args.out else P["retouch"]
    out.parent.mkdir(parents=True, exist_ok=True)
    pc.log(f"rastreando rosto + segmentação em {inp.name} ({n_frames} quadros) ...")
    t0 = time.time()
    T = track(inp, start if args.clip else None, dur if args.clip else None, n_frames, smooth_n)
    if T is None:
        pc.log("aviso: nenhum rosto — copiando sem retoque")
        shutil.copyfile(inp, out)
        pc.save_json(workdir / "retouch.json", {"applied": False, "reason": "sem rosto"})
        return
    pc.log(f"  rosto em {100 * T['detected_frac']:.0f}% dos quadros ({time.time() - t0:.0f} s); "
           f"nariz {R.get('nose_tip_reduce')}, cabelo {R.get('hairline')}, barba {R.get('beard')}, pele {R.get('skin_texture')}")

    video_only = out.with_suffix(".video.mkv") if out.suffix.lower() == ".mp4" else out
    reader = pc.FrameReader(inp, fps=pc.FPS, start=start if args.clip else None, dur=dur if args.clip else None)
    writer = pc.FrameWriter(video_only, reader.w, reader.h, crf=args.crf)
    t0 = time.time()
    i, n = 0, T["pts"].shape[0]
    for fr in reader:
        if args.clip and i >= n_frames:
            break
        j = min(i, n - 1)
        if T["conf"][j]:
            hl, sl = masks_at(T, j)
            ex = {"hair_center_v": float(T["hair_center_v"][j]), "frame_index": int(round(start * pc.FPS)) + i}
            res = apply_protect(retouch_frame(fr, T["pts"][j], hl, sl, R, protect_y, extra=ex), fr, protect_y)
        else:
            res = fr
        writer.write(res)
        i += 1
        if i % 150 == 0:
            pc.log(f"  retocando {i}/{n_frames} ({time.time() - t0:.0f} s)")
    writer.close()
    if out.suffix.lower() == ".mp4":
        # vídeo H.264 + áudio da entrada copiado (stream copy); duração travada na do trecho
        cmd = ["ffmpeg", "-y", "-i", video_only]
        if args.clip:
            cmd += ["-ss", f"{start:.6f}", "-t", f"{dur:.6f}"]
        cmd += ["-i", inp, "-map", "0:v:0"]
        if info["has_audio"]:
            cmd += ["-map", "1:a:0", "-c:a", "copy"]
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(pc.FPS),
                "-movflags", "+faststart", "-t", f"{dur:.6f}", out]
        pc.run(cmd)
        video_only.unlink(missing_ok=True)
    pc.save_json(workdir / "retouch.json", {"applied": True, "frames": i, "detected_frac": T["detected_frac"],
                                             "params": R, "input": str(inp), "output": str(out),
                                             "clip": args.clip, "seconds": round(time.time() - t0, 1)})
    pc.log(f"OK: {out} ({i} quadros, {time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
