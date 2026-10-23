"""Funções compartilhadas do reel-director.

Princípio: o Claude escreve o plan.json (edit decision list); os scripts só executam.
Reaproveita o reel_common.py da skill irmã reel-editor (mapa de tempo, probe, ffmpeg) em vez de duplicar.

Convenção de tempo: TODO tempo no plan.json (keep, shots, graphics, zoom, sfx) está na timeline ORIGINAL
(a do transcript.json). Os scripts convertem para a nova timeline com map_time() do timemap.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
EDITOR = Path(os.environ.get("REEL_EDITOR_DIR", "/mnt/skills/user/reel-editor"))
NARRA_SKILL = Path(os.environ.get("NARRACAO_SKILL_DIR", "/mnt/skills/user/narracao-voz-gabriel"))
POLISH_CANDIDATES = [os.environ.get("REEL_POLISH_DIR", ""),
                     str(SKILL.parent / "reel-polish"),  # reel-director rodando de dentro do clone do repositório
                     "/home/claude/gabrielquixabeira96-tech/editinstagraam/reel-polish",
                     str(Path.home() / "reel-polish-src" / "reel-polish")]
POLISH_REPO = "https://github.com/gabrielquixabeira96-tech/editinstagraam"
REMOTION = SKILL / "templates" / "remotion"
MODELS = SKILL / "models"

sys.path.insert(0, str(EDITOR / "scripts"))
try:
    import reel_common as rc  # noqa: E402  (skill irmã reel-editor)
except Exception as e:  # pragma: no cover
    print(f"ERRO: reel-editor não encontrado em {EDITOR} ({e}). Defina REEL_EDITOR_DIR.", file=sys.stderr)
    sys.exit(2)

FPS = 30
W, H = 1080, 1920
SR = 48000

# ------------------------------------------------------------------ utilidades
log = rc.log
die = rc.die
load_json = rc.load_json
save_json = rc.save_json
probe = rc.probe


def sha(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def fsig(p) -> str:
    p = Path(p)
    if not p.exists():
        return "missing"
    st = p.stat()
    return f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}"


def run(cmd, quiet=True, check=True, cwd=None, env=None):
    cmd = [str(c) for c in cmd]
    if cmd[0] == "ffmpeg":
        cmd[1:1] = ["-hide_banner", "-nostdin", "-y"] + (["-loglevel", "error"] if quiet else [])
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)
    if check and r.returncode != 0:
        die(f"comando falhou ({r.returncode}): {' '.join(cmd[:10])} ...\n{(r.stderr or r.stdout)[-3000:]}")
    return r


def ffprobe_json(path) -> dict:
    r = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    return json.loads(r.stdout)


def nframes(t: float) -> int:
    return int(round(t * FPS))


def q(t: float) -> float:
    """Quantiza para a grade de quadros."""
    return round(t * FPS) / FPS


def resolve(p, base=None) -> Path:
    if p is None:
        return None
    p = Path(os.path.expanduser(str(p)))
    if p.is_absolute():
        return p
    for b in [base, Path.cwd(), SKILL]:
        if b and (Path(b) / p).exists():
            return (Path(b) / p).resolve()
    return (Path(base or Path.cwd()) / p).resolve()


def polish_dir() -> Path | None:
    for c in POLISH_CANDIDATES:
        if c and (Path(c) / "scripts" / "05b_retouch.py").exists():
            return Path(c)
    return None


# ------------------------------------------------------------------ estilo e plano
def load_style(name_or_path="director") -> dict:
    p = Path(name_or_path)
    if not p.suffix:
        p = SKILL / "styles" / f"{name_or_path}.json"
    st = load_json(p)
    parent = st.pop("inherits", None)
    if parent:
        base = load_style(parent)
        for k, v in st.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k].update(v)
            else:
                base[k] = v
        st = base
    return st


PLAN_DEFAULTS = {
    "approved": False,
    "style": "director",
    "padding_ms": 80,
    "keep": [],
    "junctions": [],
    "emphasis": [],
    "zoom": [],
    "shots": [],
    "graphics": [],
    "sfx": [],
    "assets": {},
    "narration": {"mode": "original"},
    "polish": {"enabled": False},
    "audio": {},
    "ai_policy": {"allow": False, "confirmed_by_user": False, "max_credits": 0},
    "post": {},
    "cover": {},
}


def load_plan(path) -> dict:
    plan = load_json(path)
    for k, v in PLAN_DEFAULTS.items():
        plan.setdefault(k, json.loads(json.dumps(v)))
    plan["_path"] = str(Path(path).resolve())
    plan["_dir"] = str(Path(path).resolve().parent)
    return plan


def workdir_for(plan) -> Path:
    w = plan.get("workdir")
    if w:
        return resolve(w, plan["_dir"]) if not Path(w).is_absolute() else Path(w)
    src = Path(plan["source"])
    return Path(plan["_dir"]) / "work" / src.stem


def timemap(W_):
    segs, tm = rc.load_timemap(Path(W_) / "timemap.json")
    return segs, tm


def map_t(t, segs) -> float:
    return q(rc.map_time(float(t), segs)[0])


def map_span(t0, t1, segs):
    a, b = map_t(t0, segs), map_t(t1, segs)
    return a, max(b, a + 1.0 / FPS)


# ------------------------------------------------------------------ cache por etapa
class State:
    def __init__(self, W_):
        self.path = Path(W_) / "director_state.json"
        self.d = load_json(self.path) if self.path.exists() else {}

    def fresh(self, stage, sig, outputs) -> bool:
        return self.d.get(stage) == sig and all(Path(o).exists() for o in outputs)

    def mark(self, stage, sig):
        self.d[stage] = sig
        save_json(self.path, self.d)

    def clear_from(self, stages, start):
        if start in stages:
            for s in stages[stages.index(start):]:
                self.d.pop(s, None)
            save_json(self.path, self.d)


def code_sig(*names) -> str:
    return sha([fsig(SKILL / "scripts" / n) for n in names])


# ------------------------------------------------------------------ licenças
def license_table() -> dict:
    """Arquivo -> linha de licença (assets/LICENSES.md desta skill + audio/LICENSES.md do reel-editor)."""
    rows = {}
    for lic in [SKILL / "assets" / "LICENSES.md", EDITOR / "audio" / "LICENSES.md"]:
        if not lic.exists():
            continue
        for line in lic.read_text(encoding="utf-8").splitlines():
            if line.startswith("|") and not line.startswith("|--") and "Arquivo" not in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if cells and cells[0]:
                    rows[Path(cells[0].strip("`")).name] = cells
    return rows


def sfx_path(name: str) -> Path:
    """Nome curto -> arquivo de SFX licenciado (desta skill ou da biblioteca do reel-editor)."""
    st = load_style("director")
    lib = st["audio"]["sfx_library"]
    p = lib.get(name, name)
    return resolve(p, SKILL)
