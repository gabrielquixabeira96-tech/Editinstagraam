"""Funções compartilhadas por todos os scripts do reel-polish.

Princípio (o mesmo do reel-editor): o Claude DECIDE lendo `analysis.txt` e escreve
`polish.json`; os SCRIPTS EXECUTAM de forma determinística.

Regra de ouro desta skill: NUNCA alterar a timeline. Nenhum script corta, muda a
duração, o fps ou desloca o áudio. Assim o `transcript.json`/`edl.json` do
reel-editor feitos sobre o bruto continuam válidos sobre o `polished.mp4`.

Convenções:
  * pasta de trabalho: work/<nome-do-video>/polish/ (ao lado do work/ do reel-editor);
  * polish.json: arquivo de decisão (lido por todos os scripts); caminhos relativos são
    procurados na pasta do polish.json -> diretório atual -> raiz da skill;
  * saída: polished.mp4 (1080×1920, 30 fps, H.264, AAC 192 kbps, mesma duração do original).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
FPS = 30
OUT_W, OUT_H = 1080, 1920
AUDIO_SR = 48000
ANALYSIS_FPS = 5          # amostragem da análise (rosto, exposição...)
ANALYSIS_WIDTH = 960      # largura do frame reduzido usado na análise

# Nomes-padrão dentro de work/<nome>/polish/
STD = {
    "polish": "polish.json",
    "analysis": "analysis.json",
    "analysis_txt": "analysis.txt",
    "analysis_sheet": "analysis_sheet.png",
    "track": "track.json",            # trajetória do recorte usada no reenquadramento
    "reframe": "reframe.mkv",
    "stab_trf": "stab.trf",
    "stabilize": "stabilized.mkv",
    "grade": "graded.mkv",
    "beauty": "beauty.mkv",
    "retouch": "retouch.mkv",         # retoque avançado opcional (05b), depois do beauty
    "audio": "audio_polished.wav",
    "final": "polished.mp4",
    "qa": "qa_report.json",
    "compare_sheet": "compare_sheet.png",
    "state": "state.json",
}

YUNET_MODEL = SKILL_ROOT / "models" / "face_detection_yunet_2023mar.onnx"
HAAR_MODEL = SKILL_ROOT / "models" / "haarcascade_frontalface_default.xml"
RNNOISE_MODEL = SKILL_ROOT / "models" / "rnnoise_bd.rnnn"

# Enquadramentos: fração da LARGURA do quadro final ocupada pelo rosto (caixa do detector)
# e posição vertical dos olhos (fração da altura, a partir do topo).
FRAMINGS = {
    "closeup": {"face_ratio": 0.50, "eye_y": 0.38},
    "medium":  {"face_ratio": 0.38, "eye_y": 0.36},
    "wide":    {"face_ratio": 0.28, "eye_y": 0.34},
}


# --------------------------------------------------------------------------
# utilidades gerais
# --------------------------------------------------------------------------
def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"ERRO: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def load_json(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def file_sig(path) -> str:
    p = Path(path)
    if not p.exists():
        return f"{p}:ausente"
    st = p.stat()
    return f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}"


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def default_workdir(video) -> Path:
    """work/<nome>/polish/ ao lado do vídeo (mesmo work/<nome>/ do reel-editor)."""
    v = Path(video).resolve()
    return v.parent / "work" / v.stem / "polish"


def paths_in(workdir) -> dict:
    w = Path(workdir)
    return {k: w / v for k, v in STD.items()}


def resolve_path(p, base_dir=None) -> Path | None:
    if p is None:
        return None
    p = Path(os.path.expanduser(str(p)))
    if p.is_absolute():
        return p
    cands = []
    if base_dir:
        cands.append(Path(base_dir) / p)
    cands += [Path.cwd() / p, SKILL_ROOT / p]
    for c in cands:
        if c.exists():
            return c.resolve()
    return cands[0].resolve()


def fmt_t(t: float) -> str:
    m = int(t // 60)
    s = t - 60 * m
    return f"{m:02d}:{s:05.2f}"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------
BREW_FFMPEG_FULL = ["/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"]
FFMPEG_INSTALL_HINT = ("macOS: `brew install ffmpeg-full` + `export REEL_FFMPEG=$(brew --prefix ffmpeg-full)/bin/ffmpeg`; "
                       "Linux: `sudo apt install ffmpeg`")


def ffmpeg_bin() -> str:
    env = os.environ.get("REEL_FFMPEG")
    if env:
        if not Path(env).exists() and not shutil.which(env):
            die(f"REEL_FFMPEG aponta para um arquivo inexistente: {env}")
        return env
    for d in BREW_FFMPEG_FULL:
        if (Path(d) / "ffmpeg").exists():
            return str(Path(d) / "ffmpeg")
    w = shutil.which("ffmpeg")
    if w:
        return w
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        die("ffmpeg não encontrado. " + FFMPEG_INSTALL_HINT)


def ffprobe_bin():
    env = os.environ.get("REEL_FFPROBE")
    if env:
        return env
    ff = Path(ffmpeg_bin())
    cand = ff.with_name("ffprobe" + ff.suffix)
    if cand.exists():
        return str(cand)
    return shutil.which("ffprobe")


def run(cmd, quiet=True, check=True) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    if cmd[0] == "ffmpeg":
        cmd[0] = ffmpeg_bin()
        cmd[1:1] = ["-hide_banner", "-nostdin"]
        if quiet:
            cmd[3:3] = ["-loglevel", "error"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        tail = (r.stderr or "")[-3000:]
        die(f"comando falhou ({r.returncode}): {' '.join(cmd[:10])} ...\n{tail}")
    return r


def ffmpeg_filters() -> set:
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True)
    return set(re.findall(r"^\s*[A-Z.]{3}\s+(\S+)\s", r.stdout, flags=re.M))


def probe(path) -> dict:
    """{'width','height','fps','duration','has_audio','sample_rate','channels','nb_frames','rotation'}
    (largura/altura já consideram a rotação do contêiner, como o ffmpeg faz ao decodificar)."""
    fp = ffprobe_bin()
    if not fp:
        die("ffprobe não encontrado (instale o ffmpeg completo)")
    r = subprocess.run([fp, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die(f"ffprobe não conseguiu ler {path}: {r.stderr[-500:]}")
    d = json.loads(r.stdout)
    info = {"width": None, "height": None, "fps": None, "duration": None, "has_audio": False,
            "sample_rate": None, "channels": None, "vcodec": None, "acodec": None, "abitrate": None,
            "nb_frames": None, "rotation": 0}
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and info["width"] is None:
            info["width"], info["height"] = s.get("width"), s.get("height")
            info["vcodec"] = s.get("codec_name")
            rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
            n, _, dd = rate.partition("/")
            try:
                info["fps"] = float(n) / float(dd or 1)
            except ZeroDivisionError:
                info["fps"] = None
            rot = 0
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(sd["rotation"])
            info["rotation"] = rot
            if abs(rot) in (90, 270):
                info["width"], info["height"] = info["height"], info["width"]
            if s.get("nb_frames"):
                info["nb_frames"] = int(s["nb_frames"])
        if s.get("codec_type") == "audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["sample_rate"] = int(s.get("sample_rate", 0) or 0)
            info["channels"] = s.get("channels")
            info["acodec"] = s.get("codec_name")
            if s.get("bit_rate"):
                info["abitrate"] = int(s["bit_rate"])
    if d.get("format", {}).get("duration"):
        info["duration"] = float(d["format"]["duration"])
    return info


def count_frames(path) -> int:
    """Conta quadros decodificando (exato; usado no QA e no render)."""
    fp = ffprobe_bin()
    r = subprocess.run([fp, "-v", "error", "-count_packets", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0


def decode_audio(path, sr=AUDIO_SR, channels=2, start=None, dur=None):
    """Decodifica qualquer áudio/vídeo para numpy float32 (amostras, canais), amostra 0 = t 0."""
    import numpy as np
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.6f}"]
    cmd += ["-i", str(path)]
    if dur is not None:
        cmd += ["-t", f"{dur:.6f}"]
    cmd += ["-vn", "-af", "aresample=async=1:first_pts=0", "-ac", str(channels), "-ar", str(sr),
            "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        die(f"falha ao decodificar áudio de {path}: {r.stderr.decode(errors='ignore')[-500:]}")
    a = np.frombuffer(r.stdout, dtype=np.float32)
    return a.reshape(-1, channels).copy()


class FrameReader:
    """Lê quadros (BGR, numpy) de um vídeo via ffmpeg rawvideo — respeita a rotação do contêiner,
    exatamente como os outros filtros do ffmpeg enxergam o vídeo (OpenCV nem sempre respeita)."""

    def __init__(self, path, width=None, height=None, fps=None, start=None, dur=None, vf_extra=None,
                 gray=False):
        import numpy as np  # noqa: F401
        info = probe(path)
        self.src_w, self.src_h = info["width"], info["height"]
        if width and not height:
            height = int(round(self.src_h * width / self.src_w / 2)) * 2
        if height and not width:
            width = int(round(self.src_w * height / self.src_h / 2)) * 2
        self.w, self.h = (width or self.src_w), (height or self.src_h)
        self.gray = gray
        vf = []
        if fps:
            vf.append(f"fps={fps}")
        if (width or height) and (self.w != self.src_w or self.h != self.src_h):
            vf.append(f"scale={self.w}:{self.h}:flags=area")
        if vf_extra:
            vf.append(vf_extra)
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error"]
        if start is not None:
            cmd += ["-ss", f"{start:.6f}"]
        cmd += ["-i", str(path)]
        if dur is not None:
            cmd += ["-t", f"{dur:.6f}"]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        cmd += ["-an", "-f", "rawvideo", "-pix_fmt", "gray" if gray else "bgr24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 7)
        self.nbytes = self.w * self.h * (1 if gray else 3)

    def __iter__(self):
        import numpy as np
        while True:
            buf = self.proc.stdout.read(self.nbytes)
            if len(buf) < self.nbytes:
                break
            arr = np.frombuffer(buf, dtype=np.uint8)
            yield arr.reshape(self.h, self.w) if self.gray else arr.reshape(self.h, self.w, 3)
        self.proc.stdout.close()
        self.proc.wait()

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


class FrameWriter:
    """Grava quadros BGR num vídeo H.264 (intermediário quase sem perdas) via ffmpeg rawvideo."""

    def __init__(self, path, width, height, fps=FPS, crf=12, preset="ultrafast", extra_vf=None):
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-"]
        if extra_vf:
            cmd += ["-vf", extra_vf]
        cmd += ["-an", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-r", str(fps), str(path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.n = 0

    def write(self, frame):
        import numpy as np
        self.proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
        self.n += 1

    def close(self):
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode(errors="ignore")
        rc = self.proc.wait()
        if rc != 0:
            die(f"ffmpeg (gravação) falhou: {err[-2000:]}")


# --------------------------------------------------------------------------
# detector de rosto (YuNet; Haar como reserva)
# --------------------------------------------------------------------------
class FaceDetector:
    """Detecta rostos. Retorna lista de dicts {x,y,w,h,score,eyes:[(x,y),(x,y)],nose,mouth:[(x,y),(x,y)]}
    em coordenadas do frame passado. Backend 'yunet' (OpenCV FaceDetectorYN, com 5 pontos) ou
    'haar' (sem pontos: olhos estimados pela geometria da caixa)."""

    def __init__(self, backend="auto", score=0.6):
        import cv2
        self.cv2 = cv2
        self.backend = None
        self.size = None
        if backend in ("auto", "yunet") and YUNET_MODEL.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.det = cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (320, 320), score, 0.3, 5000)
                self.backend = "yunet"
            except Exception:
                self.det = None
        if self.backend is None:
            if not HAAR_MODEL.exists():
                die("nenhum detector de rosto disponível (faltam models/*.onnx e models/*.xml)")
            self.det = cv2.CascadeClassifier(str(HAAR_MODEL))
            self.backend = "haar"

    def detect(self, frame_bgr):
        """Multi-escala: o YuNet é treinado para rostos pequenos/médios; um rosto que ocupa metade do
        quadro só é detectado depois de reduzir a imagem. Tenta 1×, 0,5× e 0,25× e devolve na
        escala original."""
        out = self._detect(frame_bgr)
        if out:
            return out
        cv2 = self.cv2
        h, w = frame_bgr.shape[:2]
        for s in (0.5, 0.25):
            if min(h, w) * s < 64:
                break
            small = cv2.resize(frame_bgr, (max(16, int(w * s)), max(16, int(h * s))), interpolation=cv2.INTER_AREA)
            res = self._detect(small)
            if res:
                for f in res:
                    for k in ("x", "y", "w", "h"):
                        f[k] /= s
                    f["eyes"] = [(e[0] / s, e[1] / s) for e in f["eyes"]]
                    f["nose"] = (f["nose"][0] / s, f["nose"][1] / s)
                    f["mouth"] = [(m[0] / s, m[1] / s) for m in f["mouth"]]
                return res
        return out

    def _detect(self, frame_bgr):
        cv2 = self.cv2
        h, w = frame_bgr.shape[:2]
        out = []
        if self.backend == "yunet":
            if self.size != (w, h):
                self.det.setInputSize((w, h))
                self.size = (w, h)
            _, faces = self.det.detect(frame_bgr)
            if faces is None:
                return out
            for f in faces:
                x, y, fw, fh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
                pts = [(float(f[4 + 2 * i]), float(f[5 + 2 * i])) for i in range(5)]
                out.append({"x": x, "y": y, "w": fw, "h": fh, "score": float(f[14]),
                            "eyes": [pts[0], pts[1]], "nose": pts[2], "mouth": [pts[3], pts[4]]})
        else:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            faces = self.det.detectMultiScale(gray, 1.1, 5, minSize=(max(24, w // 20),) * 2)
            for (x, y, fw, fh) in faces:
                x, y, fw, fh = float(x), float(y), float(fw), float(fh)
                out.append({"x": x, "y": y, "w": fw, "h": fh, "score": 0.5,
                            "eyes": [(x + 0.3 * fw, y + 0.38 * fh), (x + 0.7 * fw, y + 0.38 * fh)],
                            "nose": (x + 0.5 * fw, y + 0.6 * fh),
                            "mouth": [(x + 0.35 * fw, y + 0.8 * fh), (x + 0.65 * fw, y + 0.8 * fh)]})
        out.sort(key=lambda d: -(d["w"] * d["h"]))
        return out


def pick_face(faces, prev=None):
    """Escolhe o rosto principal: o mais próximo do anterior (continuidade) ou, sem anterior, o maior."""
    if not faces:
        return None
    if prev is None:
        return faces[0]
    px, py = prev["x"] + prev["w"] / 2, prev["y"] + prev["h"] / 2
    best, bd = None, None
    for f in faces:
        cx, cy = f["x"] + f["w"] / 2, f["y"] + f["h"] / 2
        d = math.hypot(cx - px, cy - py) / max(prev["w"], 1.0)
        score = d - 0.5 * (f["w"] / max(prev["w"], 1.0))  # pequena preferência por rostos grandes
        if bd is None or score < bd:
            best, bd = f, score
    return best


# --------------------------------------------------------------------------
# suavização de trajetórias
# --------------------------------------------------------------------------
def fill_gaps(values):
    """Interpola linearmente valores None (mantendo as pontas)."""
    import numpy as np
    v = np.array([np.nan if x is None else float(x) for x in values], dtype=float)
    if np.all(np.isnan(v)):
        return v
    idx = np.arange(len(v))
    ok = ~np.isnan(v)
    v[~ok] = np.interp(idx[~ok], idx[ok], v[ok])
    return v


def smooth(values, window, poly=2):
    """Savitzky-Golay (sem jitter); janela em amostras (ímpar), reduzida se a série for curta."""
    import numpy as np
    from scipy.signal import savgol_filter
    v = np.asarray(values, dtype=float)
    n = len(v)
    if n < 5:
        return v.copy()
    window = int(window)
    if window % 2 == 0:
        window += 1
    window = max(5, min(window, n if n % 2 == 1 else n - 1))
    poly = min(poly, window - 1)
    return savgol_filter(v, window, poly, mode="interp")


# --------------------------------------------------------------------------
# polish.json
# --------------------------------------------------------------------------
DEFAULT_POLISH = {
    "source": None,
    "mode": "raw",                   # raw: bruto -> reenquadra+tudo | final: só cor/pele/áudio/nitidez
    "framing": "medium",             # closeup | medium | wide
    "fit": "auto",                   # auto | cover | smart | contain
    "static": False,                 # True: um único recorte fixo (mediana da trajetória)
    "stabilize": "auto",             # true | false | "auto" (decide pelo tremor medido)
    "look": "looks/natural.json",
    "lut": None,                     # .cube opcional (aplicado depois do look)
    "exposure": {"auto": True, "target_face_luma": 0.58, "max_gamma": 1.6},
    #   opcionais em exposure (senão vêm do look): "contrast", "lift_shadows", "highlight_rolloff"
    "white_balance": {"auto": True, "temperature_shift": 0, "tint": 0.0, "strength": 0.7},
    "denoise": "auto",               # auto | off | leve | medio
    "saturation": None,              # None = do look (natural: 1.08)
    "vibrance": None,                # None = do look (natural: 0.12)
    "vignette": None,                # 0 = sem vinheta; 1 = forte; None = do look
    "sharpen": None,                 # nitidez final (0–1.5), só luma, aplicada no render; None = do look
    "beauty_intensity": 35,          # 0–100
    "beauty": {"under_eye": True, "eye_brighten": True, "shine_reduction": True},
    # retoque avançado opcional (05b_retouch.py, MediaPipe): nariz (0–0.3 = redução da ponta/asas),
    # cabelo/barba (0–1 = força do preenchimento de falhas), pele (0–1 = uniformização de tom/manchas
    # preservando textura). protect_bottom_from: fração da altura a partir da qual NADA é alterado
    # (legenda queimada em mode final). landmark_smooth_frames: janela da suavização temporal.
    #   hairline_shape/beard_shape: "shape-up" geométrico (linha-alvo do cabelo pelos terços faciais com "M" suave,
    #   linha da bochecha e contorno do pescoço da barba, sombra mandibular) com síntese de fios ancorada ao rosto.
    #   hairline_lower_max: quanto a linha do cabelo pode descer (fração de fw); hairline_recession: recuo temporal do "M".
    "retouch": {"enabled": False, "nose_tip_reduce": 0.0, "hairline": 0.0, "beard": 0.0, "skin_texture": 0.0,
                "hairline_shape": False, "beard_shape": False, "hairline_lower_max": 0.08, "hairline_recession": 0.04,
                "protect_bottom_from": None, "landmark_smooth_frames": 7},
    "audio": {"enabled": True, "highpass_hz": 80, "denoise": "auto", "denoise_db": 12, "deesser": 0.35,
              "mud_db": -2.0, "presence_db": 2.0, "air_db": 1.5, "comp_ratio": 2.5, "comp_threshold_db": -20,
              "limiter_db": -1.5},
    "fade": {"in_s": 0.0, "out_s": 0.0},
}


def _merge(base, user):
    out = json.loads(json.dumps(base))
    for k, v in (user or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def resolve_retouch_preset(block) -> dict:
    """Bloco retouch com preset nomeado: {"preset": "gabriel", ...} carrega presets/gabriel.json (ou um caminho .json);
    as chaves explícitas do bloco sobrescrevem as do preset."""
    block = dict(block or {})
    name = block.pop("preset", None)
    if not name:
        return block
    p = Path(str(name))
    if p.suffix.lower() != ".json":
        p = SKILL_ROOT / "presets" / f"{name}.json"
    elif not p.is_absolute():
        p = SKILL_ROOT / p
    if not p.exists():
        die(f"preset de retoque não encontrado: {p}")
    base = load_json(p)
    base = dict(base.get("retouch", base))
    base = {k: v for k, v in base.items() if not k.startswith("_")}
    base.update(block)
    log(f"preset de retoque: {p.stem}" + (f" (sobrescritas: {', '.join(block)})" if block else ""))
    return base


def load_polish(path) -> dict:
    p = load_json(path)
    if not p.get("source"):
        die(f"{path}: falta o campo 'source' (vídeo de entrada)")
    if isinstance(p.get("retouch"), dict) and p["retouch"].get("preset"):
        p["retouch"] = resolve_retouch_preset(p["retouch"])
    cfg = _merge(DEFAULT_POLISH, p)
    if cfg["mode"] not in ("raw", "final"):
        die("mode deve ser 'raw' ou 'final'")
    if cfg["framing"] not in FRAMINGS:
        die(f"framing deve ser um de {list(FRAMINGS)}")
    cfg["beauty_intensity"] = clamp(float(cfg["beauty_intensity"]), 0, 100)
    r = cfg["retouch"]
    r["enabled"] = bool(r.get("enabled"))
    r["nose_tip_reduce"] = clamp(float(r.get("nose_tip_reduce") or 0), 0, 0.35)
    for k in ("hairline", "beard", "skin_texture"):
        r[k] = clamp(float(r.get(k) or 0), 0, 1)
    return cfg


def polish_source(cfg, cfg_path) -> Path:
    src = resolve_path(cfg["source"], Path(cfg_path).parent)
    if not src.exists():
        die(f"vídeo de origem não encontrado: {src}")
    return src


def load_look(path_or_name, base_dir=None) -> dict:
    """Carrega looks/*.json com herança: cada look pode ter "inherits": "natural" (ou caminho)."""
    if not path_or_name:
        path_or_name = "looks/natural.json"
    p = Path(path_or_name)
    if not p.suffix:
        p = Path("looks") / f"{p.name}.json"
    p = resolve_path(p, base_dir)
    if not p.exists():
        die(f"look não encontrado: {p}")
    look = load_json(p)
    parent = look.pop("inherits", None)
    if parent:
        base = load_look(parent, p.parent)
        look = _merge(base, look)
    look["_path"] = str(p)
    return look


def pick(cfg, look, key, default):
    """Valor de cfg[key] se definido (não None); senão do look; senão default."""
    v = cfg.get(key)
    if v is None:
        v = look.get(key)
    return default if v is None else v


def ff_filter_value(v) -> str:
    """Escapa um caminho para uso dentro de -vf/-filter_complex (dois níveis de escape)."""
    v = str(v).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    for c in ["\\", "'", "[", "]", ",", ";"]:
        v = v.replace(c, "\\" + c)
    return v
