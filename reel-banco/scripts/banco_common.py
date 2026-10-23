"""Funções compartilhadas por todos os scripts do reel-banco.

Princípio (o mesmo da reel-editor/reel-polish): os SCRIPTS extraem, medem e indexam de forma
determinística; o Claude LÊ (`segment_input.md` / `doc_input.md`) e DECIDE, escrevendo `segments.json`.

Convenções:
  * item_id     = <AAAAMMDD>_<slug-do-nome>   (data = modifiedTime do Drive ou mtime do arquivo local)
  * segment_id  = <item_id>#s03
  * mídia pesada (source.*, keyframes, figuras, clipes) -> work/banco/<item_id>/  (NUNCA no git)
  * índice leve versionado -> index/  (index.json, index.md, bm25_tokens.json, items/<id>/{item.json,
    transcript.json, segments.json, contact_sheet*.jpg, thumbs/*.jpg})
  * cache por etapa em work/banco/<item_id>/state.json (assinatura das entradas; nada é refeito sem mudança)

Variáveis de ambiente:
  REEL_BANCO_WORK   pasta de trabalho (padrão: <skill>/work/banco)
  REEL_BANCO_INDEX  pasta do índice   (padrão: <skill>/index)
  REEL_FFMPEG / REEL_FFPROBE  binários do ffmpeg (padrão: PATH)
  REEL_WHISPER_MODEL modelo faster-whisper (padrão: small)
  REEL_YUNET        caminho do modelo YuNet (padrão: ../reel-polish/models/face_detection_yunet_2023mar.onnx)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / "scripts"
REPO_ROOT = SKILL_ROOT.parent

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".3gp"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac"}
DOC_EXT = {".pdf", ".html", ".htm"}

PUBLICOS = ["plantonista", "residente", "candidato-residencia", "concursos-vestibular", "geral"]
USOS = ["fala-principal", "broll", "corte-rapido", "citacao-card"]
LICENCAS = ["propria", "terceiros", "desconhecida"]


# --------------------------------------------------------------------------
# caminhos
# --------------------------------------------------------------------------
def work_root() -> Path:
    return Path(os.environ.get("REEL_BANCO_WORK") or (SKILL_ROOT / "work" / "banco")).resolve()


def index_root() -> Path:
    return Path(os.environ.get("REEL_BANCO_INDEX") or (SKILL_ROOT / "index")).resolve()


def item_dir(item_id: str) -> Path:
    return work_root() / item_id


def index_item_dir(item_id: str) -> Path:
    return index_root() / "items" / item_id


def clips_dir() -> Path:
    return work_root() / "clips"


def yunet_model() -> Path:
    env = os.environ.get("REEL_YUNET")
    if env:
        return Path(env)
    return REPO_ROOT / "reel-polish" / "models" / "face_detection_yunet_2023mar.onnx"


def find_source(item_id: str) -> Path | None:
    d = item_dir(item_id)
    if not d.exists():
        return None
    for p in sorted(d.glob("source.*")):
        if not p.name.endswith(".part"):
            return p
    return None


# --------------------------------------------------------------------------
# log / json / utilidades
# --------------------------------------------------------------------------
_LOG_ITEM: Path | None = None


def set_log_item(item_id: str | None) -> None:
    global _LOG_ITEM
    _LOG_ITEM = item_dir(item_id) / "log.txt" if item_id else None


def log(msg: str) -> None:
    print(msg, flush=True)
    if _LOG_ITEM is not None:
        try:
            _LOG_ITEM.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_ITEM, "a", encoding="utf-8") as f:
                f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} [{Path(sys.argv[0]).name}] {msg}\n")
        except OSError:
            pass


def warn(msg: str) -> None:
    log(f"AVISO: {msg}")


def die(msg: str, code: int = 1) -> None:
    print(f"ERRO: {msg}", file=sys.stderr, flush=True)
    if _LOG_ITEM is not None:
        try:
            with open(_LOG_ITEM, "a", encoding="utf-8") as f:
                f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} [{Path(sys.argv[0]).name}] ERRO: {msg}\n")
        except OSError:
            pass
    sys.exit(code)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data, compact=False) -> None:
    """Escrita atômica. compact=True (transcript.json, bm25_tokens.json): sem indentação, 1 linha."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def fmt_t(t) -> str:
    if t is None:
        return "--:--"
    t = float(t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - 3600 * h - 60 * m
    return f"{h}:{m:02d}:{s:05.2f}" if h else f"{m:02d}:{s:05.2f}"


def sha256_file(path, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def file_sig(path) -> str:
    p = Path(path)
    if not p.exists():
        return f"{p}:ausente"
    st = p.stat()
    return f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}"


def sig(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def slugify(name: str, maxlen: int = 48) -> str:
    stem = Path(name).stem if "." in name[-6:] else name
    s = strip_accents(stem).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return (s[:maxlen].rstrip("-")) or "item"


def make_item_id(name: str, when=None) -> str:
    """when: datetime | ISO string | timestamp | None (hoje)."""
    if when is None:
        d = _dt.date.today()
    elif isinstance(when, (int, float)):
        d = _dt.datetime.fromtimestamp(when).date()
    elif isinstance(when, str):
        try:
            d = _dt.datetime.fromisoformat(when.replace("Z", "+00:00")).date()
        except ValueError:
            d = _dt.date.today()
    else:
        d = when.date() if hasattr(when, "date") else when
    return f"{d.strftime('%Y%m%d')}_{slugify(name)}"


def kind_of(name: str, mime: str | None = None) -> str:
    """video | doc | ignorado"""
    ext = Path(name).suffix.lower()
    mime = (mime or "").lower()
    if ext in VIDEO_EXT or ext in AUDIO_EXT or mime.startswith("video/") or mime.startswith("audio/"):
        return "video"
    if ext in DOC_EXT or mime in ("application/pdf", "text/html"):
        return "doc"
    return "ignorado"


def split_seg_id(seg_id: str):
    if "#" not in seg_id:
        die(f"id de segmento inválido (esperado <item_id>#sNN): {seg_id}")
    a, b = seg_id.split("#", 1)
    return a, b


# --------------------------------------------------------------------------
# tokenização (BM25 / citações)
# --------------------------------------------------------------------------
STOPWORDS = set("""a o as os um uma uns umas de do da dos das no na nos nas ao aos em por para pra pro com sem
e ou mas que se nao sim ja la ca isso esse essa este esta aquele aquela isto aquilo ele ela eles elas
eu tu voce voces nos vos me te lhe lhes meu minha seu sua nosso nossa teu tua dele dela
foi ser sao era tem ter tinha ha como mais menos muito muita muitos muitas tambem entao
quando onde porque porque pois so ate sobre entre depois antes aqui ali tipo ne ai the of and to in""".split())


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(s or "").lower()).strip()


def norm_word(w: str) -> str:
    return re.sub(r"[^\w]", "", strip_accents(w or "").lower())


def tokenize(s: str, keep_stop: bool = False) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", norm_text(s))
    out = []
    for t in toks:
        if not keep_stop and (t in STOPWORDS or len(t) < 2):
            continue
        # stemização leve: plural simples
        if len(t) > 4 and t.endswith("oes"):
            t = t[:-3] + "ao"
        elif len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


# --------------------------------------------------------------------------
# ffmpeg / ffprobe
# --------------------------------------------------------------------------
def ffmpeg_bin() -> str:
    env = os.environ.get("REEL_FFMPEG")
    if env:
        return env
    w = shutil.which("ffmpeg")
    if not w:
        die("ffmpeg não encontrado no PATH (Linux: apt install ffmpeg; macOS: brew install ffmpeg)")
    return w


def ffprobe_bin() -> str:
    env = os.environ.get("REEL_FFPROBE")
    if env:
        return env
    w = shutil.which("ffprobe")
    if not w:
        die("ffprobe não encontrado no PATH")
    return w


def run(cmd, check=True, capture=True, text=True) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=capture, text=text)
    if check and r.returncode != 0:
        err = (r.stderr or "")[-2000:] if capture and text else ""
        die(f"comando falhou ({r.returncode}): {' '.join(map(str, cmd))[:400]}\n{err}")
    return r


def probe(path) -> dict:
    r = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            check=False)
    if r.returncode != 0:
        return {"ok": False, "erro": (r.stderr or "")[-500:]}
    d = json.loads(r.stdout or "{}")
    fmt = d.get("format", {})
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    out = {"ok": True, "duration": float(fmt.get("duration") or 0) or None,
           "size": int(fmt.get("size") or 0) or None, "format": fmt.get("format_name"),
           "has_video": v is not None, "has_audio": a is not None}
    if v:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        if not rot and (v.get("tags") or {}).get("rotate"):
            rot = int(v["tags"]["rotate"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        fr = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
        try:
            n, dd = fr.split("/")
            fps = float(n) / float(dd) if float(dd) else 0.0
        except Exception:
            fps = 0.0
        out.update({"width": w, "height": h, "rotation": rot, "fps": round(fps, 3), "vcodec": v.get("codec_name")})
        if not out["duration"] and v.get("duration"):
            out["duration"] = float(v["duration"])
    if a:
        out.update({"acodec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate") or 0),
                    "channels": a.get("channels")})
    return out


def decode_audio_mono(path, sr=16000, start=None, dur=None):
    """Decodifica o áudio com ffmpeg -> numpy float32 mono. (Contorno obrigatório: faster-whisper 1.2.1
    quebra com PyAV 19 ao receber caminho de arquivo; sempre passar o array.) start/dur em s leem só um trecho
    (-ss antes do -i: busca rápida e exata em áudio), para limitar a memória em arquivos longos."""
    import numpy as np
    cmd = [ffmpeg_bin(), "-nostdin", "-v", "error"]
    if start:
        cmd += ["-ss", f"{float(start):.3f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{float(dur):.3f}"]
    cmd += ["-vn", "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        die(f"falha ao decodificar áudio de {path}: {r.stderr.decode(errors='ignore')[-400:]}")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


# --------------------------------------------------------------------------
# cache por etapa
# --------------------------------------------------------------------------
class State:
    """work/banco/<item_id>/state.json -> {"steps": {nome: {"sig", "done_at", "outputs": [...]}}}"""

    def __init__(self, item_id: str):
        self.path = item_dir(item_id) / "state.json"
        self.data = load_json(self.path) if self.path.exists() else {"steps": {}}

    def fresh(self, step: str, signature: str, outputs=()) -> bool:
        s = self.data.get("steps", {}).get(step)
        if not s or s.get("sig") != signature:
            return False
        return all(Path(o).exists() for o in (outputs or s.get("outputs", [])))

    def done(self, step: str, signature: str, outputs=(), **extra) -> None:
        self.data.setdefault("steps", {})[step] = {"sig": signature, "done_at": now_iso(),
                                                   "outputs": [str(o) for o in outputs], **extra}
        save_json(self.path, self.data)


# --------------------------------------------------------------------------
# item.json
# --------------------------------------------------------------------------
def load_item(item_id: str, required=True) -> dict | None:
    for p in (item_dir(item_id) / "item.json", index_item_dir(item_id) / "item.json"):
        if p.exists():
            return load_json(p)
    if required:
        die(f"item.json de {item_id} não encontrado (rode 02_fetch.py ou 07_doc_ingest.py antes)")
    return None


def save_item(item: dict) -> None:
    """Grava item.json na pasta de trabalho e no índice (o índice é a cópia durável e leve)."""
    item["atualizado_em"] = now_iso()
    save_json(item_dir(item["id"]) / "item.json", item)
    save_json(index_item_dir(item["id"]) / "item.json", item)


def load_transcript(item_id: str) -> dict | None:
    for p in (item_dir(item_id) / "transcript.json", index_item_dir(item_id) / "transcript.json"):
        if p.exists():
            return load_json(p)
    return None


def load_segments(item_id: str, prefer_index=False) -> list | None:
    cands = [item_dir(item_id) / "segments.json", index_item_dir(item_id) / "segments.json"]
    if prefer_index:
        cands.reverse()
    for p in cands:
        if p.exists():
            d = load_json(p)
            return d.get("segments", d) if isinstance(d, dict) else d
    return None


def py() -> str:
    return sys.executable or "python3"


def run_script(name: str, *args, check=True) -> int:
    """Roda outro script da skill (mesmo interpretador), ecoando a saída."""
    cmd = [py(), str(SCRIPTS / name), *[str(a) for a in args]]
    r = subprocess.run(cmd)
    if check and r.returncode != 0:
        die(f"{name} falhou (código {r.returncode})", r.returncode)
    return r.returncode
