"""Biblioteca de efeitos do compositor (numpy + OpenCV, quadros BGR uint8 1080x1920).

Tudo aqui é determinístico e testável isoladamente (tests/test_vfx.py). O compositor (05_compose.py) chama:
  * Reader / Writer           — leitura/escrita de quadros por pipe do ffmpeg (sem arquivos temporários)
  * ease_*                    — curvas (nunca movimento linear em tela)
  * transition(kind, A, B, p) — cut, dissolve, match, luma, whip, zoom_through
  * zoom_frame()              — punch-in / push-in em torno de um ponto focal
  * over_rgba()               — sobreposição alfa limitada à caixa do conteúdo (rápida)
  * Segmenter                 — máscara de pessoa (MediaPipe selfie segmenter, local, grátis)
  * key_green()/refine_alpha()/light_wrap()/color_match() — acabamento de chroma key
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 1080, 1920, 30


# ------------------------------------------------------------------ easing
def clamp01(x):
    return max(0.0, min(1.0, float(x)))


def ease_out_cubic(p):
    p = clamp01(p)
    return 1 - (1 - p) ** 3


def ease_in_out_cubic(p):
    p = clamp01(p)
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def ease_in_cubic(p):
    p = clamp01(p)
    return p ** 3


# ------------------------------------------------------------------ IO
class Reader:
    """Lê quadros BGR (ou GRAY/BGRA) de um vídeo a partir de `start` s, já em 1080x1920 @30 fps."""

    def __init__(self, path, start=0.0, nframes=None, pix="bgr24", vf=None, decoder=None):
        self.ch = {"bgr24": 3, "gray": 1, "bgra": 4}[pix]
        self.size = W * H * self.ch
        filt = f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}"
        if vf:
            filt = vf + "," + filt
        cmd = ["ffmpeg", "-v", "error", "-nostdin"]
        if start > 0:
            cmd += ["-ss", f"{start:.4f}"]
        if decoder:
            cmd += ["-c:v", decoder]
        cmd += ["-i", str(path)]
        if nframes:
            cmd += ["-frames:v", str(int(nframes))]
        cmd += ["-vf", filt, "-f", "rawvideo", "-pix_fmt", pix, "-"]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=self.size * 2)
        self.last = None

    def read(self):
        buf = self.p.stdout.read(self.size)
        if len(buf) < self.size:
            return self.last  # segura o último quadro (fim de arquivo)
        a = np.frombuffer(buf, np.uint8)
        self.last = a.reshape(H, W) if self.ch == 1 else a.reshape(H, W, self.ch)
        return self.last

    def close(self):
        try:
            self.p.stdout.close()
            self.p.kill()
        except Exception:
            pass


class Writer:
    def __init__(self, path, crf=12, preset="veryfast", pix_in="bgr24", codec="libx264"):
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", pix_in, "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-"]
        if codec == "ffv1":
            cmd += ["-c:v", "ffv1", "-pix_fmt", "gray" if pix_in == "gray" else "bgr0"]
        else:
            cmd += ["-c:v", codec, "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
        cmd += [str(path)]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.n = 0

    def write(self, frame):
        self.p.stdin.write(np.ascontiguousarray(frame).tobytes())
        self.n += 1

    def close(self):
        self.p.stdin.close()
        rc = self.p.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg writer terminou com código {rc}")


def read_png_rgba(path):
    """PNG RGBA -> (BGR float32 pré-multiplicado, alfa float32 0–1, bbox) ou None se vazio."""
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGRA)
    if im.shape[2] == 3:
        im = np.dstack([im, np.full(im.shape[:2], 255, np.uint8)])
    if im.shape[:2] != (H, W):
        im = cv2.resize(im, (W, H), interpolation=cv2.INTER_AREA)
    a = im[..., 3]
    ys, xs = np.nonzero(a > 2)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    return im[y0:y1, x0:x1], (int(x0), int(y0), int(x1), int(y1))


def over_rgba(base, crop_rgba, bbox, opacity=1.0):
    """Sobrepõe um recorte BGRA (alfa reto) em `base` (BGR uint8) só dentro da bbox."""
    x0, y0, x1, y1 = bbox
    roi = base[y0:y1, x0:x1].astype(np.float32)
    a = crop_rgba[..., 3:4].astype(np.float32) / 255.0 * opacity
    out = crop_rgba[..., :3].astype(np.float32) * a + roi * (1 - a)
    base[y0:y1, x0:x1] = np.clip(out, 0, 255).astype(np.uint8)
    return base


# ------------------------------------------------------------------ geometria
def zoom_frame(img, scale, fx=0.5, fy=0.42, dx=0.0, dy=0.0):
    """Escala `img` em torno do ponto focal (fx, fy em fração), mantendo 1080x1920. scale>=1 = aproxima."""
    if abs(scale - 1.0) < 1e-4 and dx == 0 and dy == 0:
        return img
    cx, cy = fx * W, fy * H
    M = np.float32([[scale, 0, cx - scale * cx + dx], [0, scale, cy - scale * cy + dy]])
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def radial_blur_zoom(img, scale, spread=0.06, n=4, fx=0.5, fy=0.5):
    """Zoom com borrão de movimento radial: média de `n` escalas entre scale e scale*(1+spread)."""
    acc = np.zeros(img.shape, np.float32)
    for i in range(n):
        s = scale * (1 + spread * i / max(1, n - 1))
        acc += zoom_frame(img, s, fx, fy).astype(np.float32)
    return (acc / n).astype(np.uint8)


def hblur(img, k):
    k = int(k)
    if k < 3:
        return img
    return cv2.blur(img, (k, 1))


# ------------------------------------------------------------------ transições
def luma(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0


def transition(kind, A, B, p):
    """Mistura A (sai) e B (entra) no progresso p (0..1). Retorna BGR uint8."""
    if kind in ("cut", None) or p >= 1:
        return B if p >= 0.5 or kind in ("cut", None) else A
    if p <= 0:
        return A
    if kind in ("dissolve", "match"):
        e = ease_in_out_cubic(p)
        return cv2.addWeighted(A, 1 - e, B, e, 0)
    if kind == "luma":
        # as altas luzes de B aparecem primeiro (fusão por luminância, borda suave de 25%)
        m = np.clip((p * 1.5 - 0.25 - (1 - luma(B))) / 0.25, 0, 1)
        m = cv2.GaussianBlur(m, (0, 0), 6)[..., None]
        return (A.astype(np.float32) * (1 - m) + B.astype(np.float32) * m).astype(np.uint8)
    if kind == "whip":
        # chicote horizontal: A sai para a esquerda e B entra da direita, com borrão de movimento
        e = ease_in_out_cubic(p)
        v = 4 * e * (1 - e)  # velocidade relativa (pico no meio)
        off = int(round(e * W))
        strip = np.concatenate([A, B], axis=1)
        frame = strip[:, off:off + W]
        return hblur(frame, 9 + 140 * v)
    if kind == "zoom_through":
        # mergulho: A aproxima até 2,2x com borrão radial; B chega de 1,35x -> 1,0
        e = ease_in_out_cubic(p)
        if p < 0.5:
            q = ease_in_cubic(p / 0.5)
            a = radial_blur_zoom(A, 1 + 1.2 * q, spread=0.04 + 0.12 * q)
            b = radial_blur_zoom(B, 1.35, spread=0.10)
            return cv2.addWeighted(a, 1 - 0.5 * q, b, 0.5 * q, 0)
        q = ease_out_cubic((p - 0.5) / 0.5)
        b = radial_blur_zoom(B, 1.35 - 0.35 * q, spread=0.10 * (1 - q))
        a = radial_blur_zoom(A, 2.2, spread=0.16)
        return cv2.addWeighted(a, 0.5 * (1 - q), b, 1 - 0.5 * (1 - q), 0)
    raise ValueError(f"transição desconhecida: {kind}")


# ------------------------------------------------------------------ segmentação / chroma
class Segmenter:
    """Máscara de pessoa (0–1) com MediaPipe Tasks (selfie_segmenter.tflite), suavizada no tempo."""

    def __init__(self, model_path, temporal=0.6):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        self.mp = mp
        self.seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)), output_confidence_masks=True,
            running_mode=vision.RunningMode.IMAGE))
        self.prev = None
        self.k = temporal

    def mask(self, bgr):
        small = cv2.resize(bgr, (432, 768), interpolation=cv2.INTER_AREA)
        rgb = np.ascontiguousarray(small[..., ::-1])
        m = self.seg.segment(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)).confidence_masks[0]
        m = np.squeeze(m.numpy_view()).astype(np.float32)
        m = cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR)
        if self.prev is not None:
            m = self.k * m + (1 - self.k) * self.prev
        self.prev = m
        return m


def key_green(bgr, similarity=0.14, blend=0.06):
    """Alfa de chroma key verde (distância em CbCr, como o filtro chromakey do ffmpeg). 1 = sujeito."""
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32) / 255.0
    # cor-chave estimada pela borda (robusto a verdes diferentes)
    border = np.concatenate([ycc[:16].reshape(-1, 3), ycc[-16:].reshape(-1, 3),
                             ycc[:, :16].reshape(-1, 3), ycc[:, -16:].reshape(-1, 3)])
    key = np.median(border, axis=0)
    d = np.sqrt((ycc[..., 1] - key[1]) ** 2 + (ycc[..., 2] - key[2]) ** 2)
    return np.clip((d - similarity) / max(blend, 1e-3), 0, 1)


def despill(bgr):
    """Remove o vazamento de verde: G limitado a max(R, B)."""
    b, g, r = [c.astype(np.float32) for c in cv2.split(bgr)]
    g2 = np.minimum(g, np.maximum(r, b))
    return cv2.merge([b, g2, r]).clip(0, 255).astype(np.uint8)


def refine_alpha(a, erode_px=2, feather_px=3):
    """Erode (tira a franja verde) + feather (borda suave) da máscara."""
    a = a.astype(np.float32)
    if erode_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode_px + 1, 2 * erode_px + 1))
        a = cv2.erode(a, k)
    if feather_px > 0:
        a = cv2.GaussianBlur(a, (0, 0), feather_px)
    return np.clip(a, 0, 1)


def color_match(fg, bg, alpha, amount=0.5):
    """Aproxima média/desvio de cor (Lab) do sujeito aos do fundo — integra a luz da cena."""
    if amount <= 0:
        return fg
    m = alpha > 0.5
    if m.sum() < 500:
        return fg
    f = cv2.cvtColor(fg, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(bg, cv2.COLOR_BGR2LAB).astype(np.float32)
    out = f.copy()
    for c in range(3):
        fm, fs = f[..., c][m].mean(), f[..., c][m].std() + 1e-3
        bm, bs = b[..., c].mean(), b[..., c].std() + 1e-3
        w = amount * (0.35 if c == 0 else 1.0)  # luminância mexe menos que a cor
        tgt_m = fm + w * (bm - fm)
        tgt_s = fs + 0.5 * w * (bs - fs)
        out[..., c] = (f[..., c] - fm) * (tgt_s / fs) + tgt_m
    return cv2.cvtColor(out.clip(0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def light_wrap(fg, bg, alpha, amount=0.35, radius=18):
    """Luz do fundo 'abraça' a borda do sujeito (integração fotográfica)."""
    if amount <= 0:
        return fg
    inv = cv2.GaussianBlur(1 - alpha, (0, 0), radius)
    edge = (inv * alpha)[..., None] * amount * 2
    bgb = cv2.GaussianBlur(bg, (0, 0), radius).astype(np.float32)
    out = fg.astype(np.float32) * (1 - edge) + bgb * edge
    return out.clip(0, 255).astype(np.uint8)


def composite(fg, bg, alpha):
    a = alpha[..., None] if alpha.ndim == 2 else alpha
    return (fg.astype(np.float32) * a + bg.astype(np.float32) * (1 - a)).astype(np.uint8)


def vignette(img, strength=0.25):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    v = 1 - strength * np.clip(r - 0.4, 0, 1)
    return (img.astype(np.float32) * v[..., None]).astype(np.uint8)
