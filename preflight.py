#!/usr/bin/env python3
"""preflight.py — checagem prévia única (1–2 min) das skills de Reels: reel-banco, reel-polish e reel-director.

Rode ANTES de qualquer edição de Reel ou ingestão no banco. Detecta, logo no início, os bloqueios que antes só
apareciam no meio do trabalho (Drive sem link, scripts ausentes, Remotion sem node_modules, Chrome do Remotion,
chave Fastcloner, reel-polish/reel-editor fora do lugar, modelos, filtros do ffmpeg, whisper, disco e CPUs) e
termina com a tabela de estimativa de tempo medida em 2 vCPU.

Uso:
  python3 preflight.py                 # só checa (não altera nada; o teste do Drive lê 64 KB de 1 arquivo/pasta)
  python3 preflight.py --fix           # corrige o que é seguro e local (symlinks, npm install, Chromium local,
                                       # arquivos que faltam a partir do repositório, pip, modelos)
  python3 preflight.py --estimar 600 55 [--graficos 11] [--sem-polish] [--sem-banco]
  python3 preflight.py --probe-id entrada=<ID> --probe-id <ID>   # testa arquivos específicos do Drive
  python3 preflight.py --no-drive      # pula a rede (só ambiente local)

Teste seguro do --fix (nunca sobre a instalação viva):
  python3 preflight.py --fix --root /tmp/x/repo --director /tmp/x/rd --links-base /tmp/x/home_claude --home /tmp/x/home

Saída: tabela OK / AVISO / FALHA com a ação de cada falha, lista "avisar o Gabriel agora" (o que depende dele) e a
tabela de estimativas. Código de saída: 0 sem FALHA; 1 com alguma FALHA.
Só biblioteca padrão (o teste do Drive chama reel-banco/scripts/drive_fetch.py, que usa `requests`).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_URL = "https://github.com/gabrielquixabeira96-tech/editinstagraam"
HERE = Path(__file__).resolve().parent

OK, AVISO, FALHA = "OK", "AVISO", "FALHA"
ROWS: list[dict] = []          # {grupo, item, status, detalhe, acao, gabriel}
FIXES: list[str] = []          # o que o --fix fez


def row(grupo, item, status, detalhe="", acao="", gabriel=False):
    ROWS.append(dict(grupo=grupo, item=item, status=status, detalhe=detalhe, acao=acao, gabriel=gabriel))


def fmt(x, nd=1):
    return f"{x:.{nd}f}".replace(".", ",")


def sh(cmd, timeout=60, cwd=None, env=None):
    """Roda um comando; nunca levanta exceção (devolve rc=127 se não existe, 124 se estourou o tempo)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired:
        return 124, "", f"tempo esgotado ({timeout} s)"
    except Exception as e:  # pragma: no cover
        return 1, "", f"{type(e).__name__}: {e}"


def symlink(link: Path, target: Path) -> bool:
    """Cria link -> target só se `link` não existe (nunca sobrescreve nada)."""
    if link.exists() or link.is_symlink():
        return False
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=target.is_dir())
    FIXES.append(f"symlink {link} -> {target}")
    return True


# ------------------------------------------------------------------ máquina
def effective_cpus() -> float:
    n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    for f in ("/sys/fs/cgroup/cpu.max",):  # cgroup v2: "quota period"
        try:
            q, p = Path(f).read_text().split()[:2]
            if q != "max":
                n = min(n, int(q) / int(p))
        except Exception:
            pass
    try:  # cgroup v1
        q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if q > 0:
            n = min(n, q / p)
    except Exception:
        pass
    return float(n)


def mem_available_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except Exception:
        pass
    return None


def check_machine(a):
    g = "Máquina"
    cpus = effective_cpus()
    st = OK if cpus >= 2 else AVISO
    row(g, "CPUs efetivas", st, f"{cpus:g} vCPU (estimativas medidas em 2 vCPU)",
        "" if st == OK else "vai demorar mais que a tabela; avise o Gabriel do prazo maior")
    mem = mem_available_gb()
    if mem is not None:
        st = OK if mem >= 4 else (AVISO if mem >= 2.5 else FALHA)
        row(g, "RAM disponível", st, f"{fmt(mem)} GB (whisper usa ~1,5–2,5 GB por janela)",
            "" if st == OK else "feche processos pesados antes de transcrever/renderizar")
    seen = set()
    for p in [a.links_base, Path(tempfile.gettempdir()), a.home]:
        try:
            p = Path(p)
            q = p if p.exists() else p.parent
            dev = os.stat(q).st_dev
            if dev in seen:
                continue
            seen.add(dev)
            free = shutil.disk_usage(q).free / 1e9
        except Exception:
            continue
        st = OK if free >= 15 else (AVISO if free >= 8 else FALHA)
        row(g, f"Disco livre ({q})", st, f"{fmt(free)} GB livres (cota fixa; bruto HEVC ~2 MB/s + intermediários ~3×)",
            "" if st == OK else "apague work/ antigos (mídia mora no Drive) antes de começar")
    py = sys.version_info
    row(g, "Python", OK if py >= (3, 10) else FALHA, sys.version.split()[0], "" if py >= (3, 10) else "precisa 3.10+")
    rc, out, _ = sh(["git", "--version"])
    row(g, "git", OK if rc == 0 else FALHA, out.strip() or "ausente", "" if rc == 0 else "apt-get install git")
    rc, out, _ = sh(["node", "-v"])
    m = re.match(r"v(\d+)", out.strip())
    st = OK if (rc == 0 and m and int(m.group(1)) >= 18) else FALHA
    row(g, "node", st, out.strip() or "ausente", "" if st == OK else "Remotion precisa Node 18+")
    rc, out, _ = sh(["npm", "-v"])
    row(g, "npm", OK if rc == 0 else FALHA, out.strip() or "ausente", "" if rc == 0 else "instale npm (vem com o Node)")


# ------------------------------------------------------------------ repositório e instalações
DIRECTOR_FILES = ["SKILL.md", "scripts/run_all.py", "scripts/dr_common.py", "scripts/vfx.py", "scripts/01_ingest.py",
                  "scripts/02_direct.py", "scripts/04_graphics.py", "scripts/05_compose.py", "scripts/06_audio.py",
                  "scripts/07_qa.py", "scripts/08_post.py", "styles/director.json",
                  "templates/remotion/render.mjs", "templates/remotion/package.json",
                  "templates/remotion/package-lock.json", "templates/remotion/src/index.jsx",
                  "templates/remotion/public/Poppins-Bold.ttf"]
DIRECTOR_MODELS = ["models/face_landmarker.task", "models/selfie_multiclass_256x256.tflite",
                   "models/selfie_segmenter.tflite", "models/deeplab_v3.tflite"]
BANCO_FILES = ["SKILL.md", "config.json", "scripts/drive_fetch.py", "scripts/run_ingest.py", "scripts/09_search.py",
               "scripts/10_clip.py", "index/index.json"]
POLISH_FILES = ["SKILL.md", "scripts/run_all.py", "scripts/05b_retouch.py", "presets"]


def check_repo(a):
    g = "Repositório"
    root = a.root
    have = all((root / s).exists() for s in ("reel-banco", "reel-polish", "reel-director"))
    if not have and a.fix and (not root.exists() or not any(root.iterdir())):
        env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")
        rc, out, err = sh(["git", "clone", REPO_URL, str(root)], timeout=600, env=env)
        if rc == 0:
            FIXES.append(f"git clone {REPO_URL} {root}")
            have = True
        else:
            row(g, "clone", FALHA, err.strip()[-200:], "use add_repo (gabrielquixabeira96-tech/editinstagraam, push) e rode de novo")
    if not have:
        row(g, f"clone em {root}", FALHA, "faltam reel-banco/, reel-polish/ ou reel-director/",
            f"git clone {REPO_URL} {root}  (ou --fix com --root numa pasta vazia)")
        return False
    rc, out, _ = sh(["git", "-C", str(root), "log", "-1", "--format=%h %cs %s"])
    row(g, f"clone em {root}", OK, out.strip()[:70] if rc == 0 else "sem .git (cópia solta)")
    for skill, files in (("reel-banco", BANCO_FILES), ("reel-polish", POLISH_FILES), ("reel-director", DIRECTOR_FILES)):
        miss = [f for f in files if not (root / skill / f).exists()]
        row(g, f"{skill}/ no clone", OK if not miss else FALHA, "completo" if not miss else "faltam: " + ", ".join(miss[:4]),
            "" if not miss else f"git -C {root} pull --ff-only (ou clone novo)")
    return True


def check_director(a):
    """Instalação da reel-director EM USO (padrão: a viva em /home/claude/reel-director-skill)."""
    g = "reel-director"
    d = a.director
    src = a.root / "reel-director"
    if not d.exists() and a.fix and src.exists():
        symlink(d, src)
    if not d.exists():
        row(g, f"instalação {d}", FALHA, "pasta não existe",
            f"--fix cria symlink para {src}; sem clone: peça o reel-director-skill.zip ao Gabriel", gabriel=not src.exists())
        return
    miss = [f for f in DIRECTOR_FILES + DIRECTOR_MODELS if not (d / f).exists()]
    if miss and a.fix and src.exists() and src.resolve() != d.resolve():
        copied = []

        def _copy_missing(s_, t_):  # nunca sobrescreve o que já existe na instalação em uso
            if not os.path.exists(t_):
                shutil.copy2(s_, t_)
                copied.append(t_)
        shutil.copytree(src, d, dirs_exist_ok=True, copy_function=_copy_missing, symlinks=False,
                        ignore=shutil.ignore_patterns("node_modules", "work", "out", "__pycache__", "*.pyc"))
        if copied:
            FIXES.append(f"copiados {len(copied)} arquivo(s) que faltavam de {src} para {d} (nada sobrescrito)")
        miss = [f for f in DIRECTOR_FILES + DIRECTOR_MODELS if not (d / f).exists()]
    real = d.resolve()
    row(g, "scripts/estilos/modelos", OK if not miss else FALHA,
        f"{real}" + ("" if not miss else f" — faltam: {', '.join(miss[:4])}{'…' if len(miss) > 4 else ''}"),
        "" if not miss else f"--fix copia do clone ({src}); só SKILL.md na sessão = falta a pasta scripts/")
    # caminhos que a documentação usa
    for name in ("reel-director-skill", "reel-director"):
        p = a.links_base / name
        if not p.exists() and a.fix:
            symlink(p, real)
        st = OK if p.exists() else AVISO
        row(g, f"caminho {p}", st, f"-> {p.resolve()}" if p.exists() else "ausente",
            "" if st == OK else f"ln -s {real} {p}  (SKILL.md e reel-banco usam este caminho)")


def remotion_versions(rem: Path):
    try:
        want = json.loads((rem / "package.json").read_text())["dependencies"].get("remotion")
    except Exception:
        want = None
    try:
        have = json.loads((rem / "node_modules" / "remotion" / "package.json").read_text())["version"]
    except Exception:
        have = None
    return want, have


def find_local_chromium():
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    pats = ["chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
            "chromium_headless_shell-*/chrome-linux/headless_shell",
            "chromium-*/chrome-linux64/chrome", "chromium-*/chrome-linux/chrome"]
    for pat in pats:
        hits = sorted(glob.glob(os.path.join(base, pat)), reverse=True)
        for h in hits:
            if os.access(h, os.X_OK):
                return h
    return None


def check_remotion(a):
    g = "Remotion"
    rem = a.director / "templates" / "remotion"
    if not (rem / "package.json").exists():
        row(g, "templates/remotion", FALHA, "package.json ausente", "ver linha 'scripts/estilos/modelos'")
        return
    nm = rem / "node_modules"
    needed = [nm / "@remotion" / "bundler", nm / "@remotion" / "renderer", nm / "remotion"]
    if not all(p.exists() for p in needed) and a.fix:
        cmd = ["npm", "ci" if (rem / "package-lock.json").exists() and not nm.exists() else "install",
               "--no-audit", "--no-fund"]
        rc, out, err = sh(cmd, timeout=900, cwd=str(rem))
        FIXES.append(f"{' '.join(cmd)} em {rem} -> rc {rc}")
        if rc != 0 and cmd[1] == "ci":
            rc, out, err = sh(["npm", "install", "--no-audit", "--no-fund"], timeout=900, cwd=str(rem))
            FIXES.append(f"npm install em {rem} -> rc {rc}")
    if not all(p.exists() for p in needed):
        row(g, "node_modules", FALHA, "faltam @remotion/bundler|renderer (ERR_MODULE_NOT_FOUND no graphics)",
            f"cd {rem} && npm install   (ou --fix)")
        return
    want, have = remotion_versions(rem)
    rc, out, err = sh(["node", "--input-type=module", "-e",
                       "await import('@remotion/bundler'); await import('@remotion/renderer'); console.log('ok')"],
                      timeout=60, cwd=str(rem))
    st = OK if rc == 0 and (not want or want == have) else (AVISO if rc == 0 else FALHA)
    row(g, "node_modules", st, f"remotion {have} (package.json {want}); import {'ok' if rc == 0 else 'FALHOU'}",
        "" if st == OK else (f"cd {rem} && npm ci" if rc == 0 else (err.strip()[-160:] + f" -> cd {rem} && npm ci")))

    # navegador
    render = (rem / "render.mjs").read_text(errors="replace") if (rem / "render.mjs").exists() else ""
    sup_env = "REMOTION_BROWSER_EXECUTABLE" in render
    sup_file = "browser-executable.txt" in render
    local = find_local_chromium()
    cfg_file = rem / "browser-executable.txt"
    env_exe = os.environ.get("REMOTION_BROWSER_EXECUTABLE")
    file_exe = cfg_file.read_text().strip() if cfg_file.exists() else None
    own = list((nm / ".remotion").glob("chrome-headless-shell*")) if (nm / ".remotion").exists() else []
    if not sup_file and a.fix:  # render.mjs antigo (sem navegador local, ou só env): atualiza a partir do clone
        repo_render = a.root / "reel-director" / "templates" / "remotion" / "render.mjs"
        if repo_render.exists() and repo_render.resolve() != (rem / "render.mjs").resolve() \
                and "browser-executable.txt" in repo_render.read_text(errors="replace"):
            shutil.copy2(rem / "render.mjs", rem / "render.mjs.bak")
            shutil.copy2(repo_render, rem / "render.mjs")
            FIXES.append(f"render.mjs atualizado do clone (backup render.mjs.bak) em {rem}")
            sup_env = sup_file = True
    if sup_file and local and not (file_exe and os.path.exists(file_exe)) and not env_exe and a.fix:
        cfg_file.write_text(local + "\n")
        FIXES.append(f"{cfg_file} = {local}")
        file_exe = local
    exe = env_exe if (sup_env and env_exe and os.path.exists(env_exe)) else (
        file_exe if (sup_file and file_exe and os.path.exists(file_exe)) else None)
    if exe:
        rc, out, _ = sh([exe, "--version"], timeout=20)
        how = "env REMOTION_BROWSER_EXECUTABLE" if exe == env_exe else "browser-executable.txt"
        row(g, "Chromium local", OK if rc == 0 else FALHA, f"{out.strip() or '?'} via {how}",
            "" if rc == 0 else f"{exe} não executa; aponte para outro em /opt/pw-browsers")
    elif own:
        row(g, "Chromium local", OK, f"Chrome do próprio Remotion já baixado ({own[0].name})")
    elif sup_env and local:
        row(g, "Chromium local", AVISO, f"render.mjs aceita só a env; local disponível: {local}",
            f"export REMOTION_BROWSER_EXECUTABLE={local}  (antes do run_all; o 04_graphics herda o ambiente)")
    elif local:
        row(g, "Chromium local", FALHA, "render.mjs desta instalação não aceita navegador local -> Remotion tenta baixar o Chrome",
            "--fix copia o render.mjs do clone; NUNCA rode `playwright install`")
    else:
        row(g, "Chromium local", FALHA, "nenhum Chromium em /opt/pw-browsers e Remotion sem Chrome próprio",
            "não rode `playwright install`; avise o Gabriel (rede bloqueia o download)", gabriel=True)


def polish_candidates(a):
    """Mesma ordem do dr_common.py da reel-director (o do clone também procura ao lado da própria pasta)."""
    dr = (a.director / "scripts" / "dr_common.py")
    sib = str(a.director.resolve().parent / "reel-polish") if dr.exists() and "SKILL.parent" in dr.read_text(
        errors="replace") else ""
    return [os.environ.get("REEL_POLISH_DIR", ""), sib,
            str(a.links_base / "gabrielquixabeira96-tech" / "editinstagraam" / "reel-polish"),
            str(a.home / "reel-polish-src" / "reel-polish")]


def check_sisters(a):
    g = "Skills irmãs"
    # ~/reel-polish-src: usado pela reel-banco (SKILL.md, run_ingest COMMIT) e pela reel-director (polish_dir)
    rps = a.home / "reel-polish-src"
    if not rps.exists() and a.fix:
        symlink(rps, a.root)
    ok_rps = (rps / "reel-banco" / "SKILL.md").exists() and (rps / "reel-polish" / "scripts" / "05b_retouch.py").exists()
    row(g, f"{rps}", OK if ok_rps else AVISO, f"-> {rps.resolve()}" if rps.exists() else "ausente",
        "" if ok_rps else f"ln -s {a.root} {rps}  (ou --fix)")
    found = next((c for c in polish_candidates(a) if c and (Path(c) / "scripts" / "05b_retouch.py").exists()), None)
    row(g, "reel-polish (como a reel-director procura)", OK if found else FALHA, found or "nenhum candidato existe",
        "" if found else f"--fix cria ~/reel-polish-src -> {a.root}; ou export REEL_POLISH_DIR={a.root}/reel-polish")
    ed = Path(os.environ.get("REEL_EDITOR_DIR", "/mnt/skills/user/reel-editor"))
    need = ["scripts/reel_common.py", "scripts/01_transcribe.py", "scripts/02_apply_edl.py", "scripts/03_subtitles.py",
            "scripts/05_audio.py", "scripts/06_render.py"]
    miss = [f for f in need if not (ed / f).exists()]
    row(g, "reel-editor", OK if not miss else FALHA, str(ed) if not miss else f"{ed}: faltam {', '.join(miss[:3])}",
        "" if not miss else "peça ao Gabriel para ativar a skill reel-editor (ou o zip) e defina REEL_EDITOR_DIR",
        gabriel=bool(miss))
    nar = Path(os.environ.get("NARRACAO_SKILL_DIR", "/mnt/skills/user/narracao-voz-gabriel"))
    st = OK if (nar / "SKILL.md").exists() else AVISO
    row(g, "narracao-voz-gabriel", st, str(nar) if st == OK else "ausente",
        "" if st == OK else "sem ela, só voz original (avise o Gabriel se o Reel pedir narração)")


def check_fastcloner(a):
    g = "Narração"
    if os.environ.get("FASTCLONER_API_KEY"):
        row(g, "FASTCLONER_API_KEY", OK, "definida no ambiente (valor não exibido)")
        return
    cands = [Path.cwd(), a.links_base / "reel_voz", a.links_base, a.director, a.director / "work", a.root]
    cands += [Path(p).parent for p in glob.glob(str(a.director / "work" / "*" / ".env"))]
    for c in cands:
        f = c / ".env"
        try:
            txt = f.read_text(errors="replace") if f.is_file() else ""
        except Exception:
            txt = ""
        if re.search(r"^\s*(export\s+)?FASTCLONER_API_KEY\s*=\s*\S+", txt, re.M):
            mode = f.stat().st_mode & 0o777
            st = OK if mode & 0o077 == 0 else AVISO
            row(g, "FASTCLONER_API_KEY", st, f"em {f} (modo {oct(mode)}; valor não exibido)",
                f"carregue antes: set -a && . {f} && set +a" + ("" if st == OK else f"; chmod 600 {f}"))
            return
    row(g, "FASTCLONER_API_KEY", AVISO, "ausente no ambiente e em .env conhecidos -> narração clonada indisponível",
        "se o Reel usar narração Fastcloner, peça AGORA a chave (começa com aiv_) ao Gabriel; guardar só em .env "
        "com umask 077; sem ela, só voz original", gabriel=True)


# ------------------------------------------------------------------ Drive
def ssl_ctx():
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    try:
        return ssl.create_default_context(cafile=ca) if ca and os.path.exists(ca) else ssl.create_default_context()
    except Exception:
        return ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kw):
        return None


def folder_probe(folder_id: str):
    """(compartilhada?, [file_ids], detalhe). Página pública da pasta: 200 = link ativo; 302 login/401 = não."""
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(), urllib.request.HTTPSHandler(context=ssl_ctx()),
                                         _NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 preflight/1.0"})
    try:
        with opener.open(req, timeout=30) as r:
            html = r.read(2_000_000).decode("utf-8", "replace")
        ids = list(dict.fromkeys(re.findall(r"/file/d/([A-Za-z0-9_-]{20,})", html)))
        return True, ids, f"link ativo, {len(ids)} arquivo(s) visível(is)"
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "") if e.headers else ""
        if e.code in (301, 302, 303, 307, 308) and "accounts.google.com" in loc:
            return False, [], "Drive pediu login (pasta NÃO compartilhada por link)"
        if e.code in (401, 403, 404):
            return False, [], f"HTTP {e.code}: pasta não compartilhada por link (ou id errado)"
        return None, [], f"HTTP {e.code} inesperado"
    except Exception as e:
        return None, [], f"erro de rede: {type(e).__name__}: {str(e)[:100]}"


def file_probe(drive_fetch: Path, file_id: str, nbytes=65536):
    """Usa o drive_fetch existente, abortando depois dos primeiros bytes. -> (rc, detalhe)."""
    txt = drive_fetch.read_text(errors="replace")
    if "--probe-bytes" in txt:
        rc, out, err = sh([sys.executable, str(drive_fetch), file_id, "-", "--probe-bytes", str(nbytes)], timeout=90)
        return rc, (out.strip() or err.strip().splitlines()[-1] if (out.strip() or err.strip()) else "")
    # drive_fetch antigo (sem --probe-bytes): baixa para um temporário e mata o processo no primeiro byte gravado
    with tempfile.TemporaryDirectory(prefix="preflight_") as td:
        dest = os.path.join(td, "probe.bin")
        p = subprocess.Popen([sys.executable, str(drive_fetch), file_id, dest],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        t0 = time.time()
        while p.poll() is None and time.time() - t0 < 90:
            if os.path.exists(dest + ".part") and os.path.getsize(dest + ".part") > 0:
                p.kill(); p.wait()
                return 0, "PROBE OK (primeiros bytes recebidos; processo abortado)"
            time.sleep(0.2)
        if p.poll() is None:
            p.kill()
        out, err = p.communicate()
        return p.returncode, (err.strip().splitlines() or [""])[-1]


def check_drive(a):
    g = "Drive (link)"
    cfg_p = a.root / "reel-banco" / "config.json"
    df = a.root / "reel-banco" / "scripts" / "drive_fetch.py"
    try:
        cfg = json.loads(cfg_p.read_text())["drive"]
    except Exception as e:
        row(g, "config.json", FALHA, f"{cfg_p}: {e}", "restaure reel-banco/config.json do repositório")
        return
    # itens do índice: drive_id -> tamanho (para escolher o arquivo MENOR de cada pasta)
    sizes = {}
    for p in glob.glob(str(a.root / "reel-banco" / "index" / "items" / "*" / "item.json")):
        try:
            it = json.loads(Path(p).read_text())
            o = it.get("origem") or {}
            if isinstance(o, dict) and o.get("drive_id"):
                sizes[o["drive_id"]] = it.get("tamanho") or 1 << 60
        except Exception:
            pass
    probes = {}  # pasta -> [ids]
    for spec in a.probe_id or []:
        k, _, v = spec.partition("=")
        k, v = (k, v) if v else ("extra", k)
        probes.setdefault(k, []).append(v)
    for key, label in (("entrada", "01_entrada"), ("processado", "02_processado")):
        fid = cfg.get(key)
        shared, ids, det = folder_probe(fid) if fid else (None, [], "id ausente no config.json")
        acao = (f"Gabriel: no Drive, pasta {label} -> Compartilhar -> Acesso geral: 'Qualquer pessoa com o link' "
                f"-> Leitor (drive_fetch sai com código 3 sem isso)")
        if shared is False:
            row(g, f"{label} (pasta)", FALHA, det, acao, gabriel=True)
            continue
        if shared is None:
            row(g, f"{label} (pasta)", AVISO, det, "confira a rede/proxy e rode de novo")
        targets = probes.pop(key, None)
        if not targets and ids:
            targets = [sorted(ids, key=lambda i: sizes.get(i, 1 << 61))[0]]
        if not targets:
            row(g, f"{label}", OK if shared else AVISO, det + "; pasta vazia: nenhum arquivo para testar",
                "" if shared else "passe --probe-id " + key + "=<ID> para testar um arquivo")
            continue
        for t in targets:
            if not df.exists():
                row(g, f"{label} arquivo {t[:10]}…", FALHA, "drive_fetch.py ausente", "git pull no clone")
                continue
            rc, d = file_probe(df, t)
            if rc == 0:
                row(g, f"{label}", OK, f"{det}; arquivo {t[:12]}… baixa (64 KB lidos)")
            elif rc == 3:
                row(g, f"{label} arquivo {t[:12]}…", FALHA, "drive_fetch código 3: não compartilhado por link", acao,
                    gabriel=True)
            else:
                row(g, f"{label} arquivo {t[:12]}…", AVISO, f"drive_fetch código {rc}: {d[:120]}",
                    "4 = HTML inesperado/cota/5xx: tente de novo em alguns minutos; requests ausente -> pip install requests")
    for key, ids in probes.items():
        for t in ids:
            rc, d = file_probe(df, t)
            row(g, f"--probe-id {key} {t[:12]}…", OK if rc == 0 else (FALHA if rc == 3 else AVISO),
                d[:120], "" if rc == 0 else "compartilhe por link (Leitor)" if rc == 3 else "tente de novo",
                gabriel=(rc == 3))


# ------------------------------------------------------------------ mídia: ffmpeg, modelos, Python, whisper
FILTERS_FAIL = ["ass", "subtitles", "scale", "crop", "format", "xfade", "chromakey", "despill", "overlay", "gblur",
                "boxblur", "loudnorm", "sidechaincompress", "amix", "adelay", "hqdn3d", "curves", "colorbalance",
                "colortemperature", "huesaturation", "vibrance", "vignette", "unsharp", "afftdn", "deesser",
                "equalizer", "acompressor", "alimiter", "scdet", "silencedetect", "astats"]
FILTERS_WARN = {"vidstabdetect": "sem estabilização (vid.stab)", "vidstabtransform": "sem estabilização (vid.stab)",
                "arnndn": "sem RNNoise (usa afftdn)", "lut3d": "sem LUT 3D", "zscale": "HDR->BT.709 limitado",
                "tonemap": "HDR->BT.709 limitado"}


def check_ffmpeg(a):
    g = "ffmpeg"
    rc, out, _ = sh(["ffmpeg", "-hide_banner", "-version"])
    if rc != 0:
        row(g, "ffmpeg", FALHA, "ausente", "apt-get install ffmpeg (com libass, libvidstab, arnndn)")
        return
    ver = out.splitlines()[0] if out else "?"
    rc2, _, _ = sh(["ffprobe", "-version"])
    rc, fl, _ = sh(["ffmpeg", "-hide_banner", "-filters"])
    names = set()
    for line in fl.splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r"[A-Z.|]{2,4}", parts[0]):
            names.add(parts[1])
    miss = [f for f in FILTERS_FAIL if f not in names]
    row(g, "versão / ffprobe", OK if rc2 == 0 else FALHA, ver[:60], "" if rc2 == 0 else "ffprobe ausente")
    lib = "libass" in out
    row(g, "filtros essenciais (libass, xfade, chromakey, loudnorm…)", OK if not miss else FALHA,
        "todos presentes" + (" (libass)" if lib else "") if not miss else "faltam: " + ", ".join(miss),
        "" if not miss else "instale um ffmpeg completo (ex.: apt-get install ffmpeg)")
    wm = sorted({FILTERS_WARN[f] for f in FILTERS_WARN if f not in names})
    row(g, "vid.stab, arnndn, lut3d, zscale", OK if not wm else AVISO, "presentes" if not wm else "; ".join(wm),
        "" if not wm else "a reel-polish degrada sozinha; informe no relatório")


def check_models(a):
    g = "Modelos"
    pm = a.root / "reel-polish" / "models"
    for f, sev, why in (("face_detection_yunet_2023mar.onnx", FALHA, "YuNet: rosto (polish e banco)"),
                        ("haarcascade_frontalface_default.xml", AVISO, "fallback de rosto"),
                        ("rnnoise_bd.rnnn", AVISO, "arnndn (ruído)")):
        ok = (pm / f).exists()
        row(g, f"reel-polish/{f}", OK if ok else sev, why if ok else f"ausente — {why}",
            "" if ok else f"git -C {a.root} checkout -- reel-polish/models (vem no git)")
    urls = {}
    try:
        txt = (a.root / "reel-polish" / "scripts" / "05b_retouch.py").read_text()
        m1 = re.search(r'LM_URL\s*=\s*"([^"]+)"', txt)
        m2 = re.search(r'SEG_URL\s*=\s*\(?\s*"([^"]+)"\s*"?([^")]*)"?', txt)
        if m1:
            urls["face_landmarker.task"] = m1.group(1)
        if m2:
            urls["selfie_multiclass_256x256.tflite"] = m2.group(1) + (m2.group(2) or "")
    except Exception:
        pass
    for f in ("face_landmarker.task", "selfie_multiclass_256x256.tflite"):
        p = pm / f
        if not p.exists() and a.fix:
            alt = a.director / "models" / f
            if alt.exists():
                shutil.copy2(alt, p); FIXES.append(f"copiado {alt} -> {p}")
            elif f in urls:
                try:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler(),
                                                         urllib.request.HTTPSHandler(context=ssl_ctx()))
                    with opener.open(urls[f], timeout=120) as r, open(str(p) + ".part", "wb") as w:
                        shutil.copyfileobj(r, w)
                    os.replace(str(p) + ".part", p); FIXES.append(f"baixado {urls[f]} -> {p}")
                except Exception as e:
                    FIXES.append(f"falhou baixar {f}: {e}")
        ok = p.exists()
        row(g, f"reel-polish/{f} (MediaPipe)", OK if ok else AVISO, "presente" if ok else "ausente (fora do git)",
            "" if ok else "o 05b_retouch baixa na 1ª execução; ou --fix agora")


PY_MODS = [  # (módulo, pip, gravidade, para quê)
    ("numpy", "numpy", FALHA, "todas"), ("scipy", "scipy", FALHA, "polish/editor"),
    ("cv2", "opencv-python-headless", FALHA, "todas"), ("PIL", "Pillow", FALHA, "banco/director"),
    ("mediapipe", "mediapipe", FALHA, "retoque 05b e segmentação"),
    ("faster_whisper", "faster-whisper", FALHA, "transcrição"), ("requests", "requests", FALHA, "drive_fetch"),
    ("rank_bm25", "rank_bm25", FALHA, "busca do banco"), ("fitz", "pymupdf", AVISO, "PDF no banco"),
    ("playwright", "playwright", AVISO, "HTML no banco (NÃO rode `playwright install`)"),
]


def check_python(a):
    g = "Python"
    code = ("import importlib,json\nr={}\n"
            f"for m in {[m for m, *_ in PY_MODS]!r}:\n"
            "    try:\n        x=importlib.import_module(m); r[m]=str(getattr(x,'__version__','ok'))\n"
            "    except Exception as e: r[m]=None\nprint(json.dumps(r))")

    def probe():
        rc, out, err = sh([sys.executable, "-c", code], timeout=120)
        try:
            return json.loads(out.strip().splitlines()[-1])
        except Exception:
            return {}
    res = probe()
    miss = [(m, pip) for m, pip, *_ in PY_MODS if not res.get(m)]
    if miss and a.fix:
        pkgs = [pip for _, pip in miss]
        rc, out, err = sh([sys.executable, "-m", "pip", "install", "--break-system-packages", "-q", *pkgs], timeout=900)
        FIXES.append(f"pip install {' '.join(pkgs)} -> rc {rc}")
        res = probe()
    bad = [(m, pip, sev, why) for m, pip, sev, why in PY_MODS if not res.get(m)]
    okm = [f"{m} {res[m]}" for m, *_ in PY_MODS if res.get(m)]
    row(g, "módulos", OK, ", ".join(okm)[:220]) if okm else None
    for m, pip, sev, why in bad:
        row(g, m, sev, f"ausente ({why})", f"pip install --break-system-packages {pip}  (ou --fix)")


def check_whisper(a):
    g = "Whisper"
    model = os.environ.get("REEL_WHISPER_MODEL", "small")
    hubs = []
    if os.environ.get("HF_HOME"):
        hubs.append(Path(os.environ["HF_HOME"]) / "hub")
    hubs += [a.home / ".cache" / "huggingface" / "hub", Path("/home/claude/.cache/huggingface/hub"),
             Path.home() / ".cache" / "huggingface" / "hub"]

    def found():
        for h in dict.fromkeys(hubs):
            hits = glob.glob(str(h / f"models--Systran--faster-whisper-{model}" / "snapshots" / "*" / "model.bin"))
            if hits:
                return hits[0]
        return None
    hit = found()
    if not hit and a.fix:
        rc, out, err = sh([sys.executable, "-c", f"from faster_whisper import WhisperModel; WhisperModel('{model}', "
                           "device='cpu', compute_type='int8')"], timeout=900)
        FIXES.append(f"download whisper {model} -> rc {rc}")
        hit = found()
    row(g, f"modelo faster-whisper '{model}'", OK if hit else AVISO,
        f"em cache ({Path(hit).parent.parent.parent.parent})" if hit else "não está no cache (~480 MB na 1ª transcrição)",
        "" if hit else "--fix baixa agora (ou conte +1–3 min na 1ª transcrição)")


# ------------------------------------------------------------------ estimativas
MEDIDAS = [  # medidas reais de 2026-10-23 em 2 vCPU
    ("Transcrição faster-whisper small", "~0,45 × duração do áudio"),
    ("Download do Drive (drive_fetch)", "28–100 MB/s (bruto HEVC de celular ≈ 1,7–2,1 MB por s de vídeo)"),
    ("Ingestão no banco (baixar+transcrever+cenas+keyframes)", "vídeo de 10 min -> ~6–8 min (0,6–0,8 × duração)"),
    ("Pré-corte de 55 s a partir de HEVC 1920×1080", "~7 min (≈ 7,6 s por s de vídeo cortado)"),
    ("reel-polish completo (reenquadre, estab., cor, pele, retoque gabriel)", "~30 min para ~55 s (≈ 33 s por s)"),
    ("   dos quais retoque 05b", "~13 min para ~55 s (≈ 14 s por s)"),
    ("reel-director graphics -> render final (11 gráficos Remotion)", "~10 min para ~55 s"),
]


def estimar(duracao_bruto_s: float, duracao_reel_s: float, n_graficos: int = 11, cpus: float | None = None,
            polish: bool = True, banco: bool = True, tamanho_bytes: float | None = None) -> dict:
    """Estimativa (min, max) em minutos por etapa e total, a partir das medidas de 2026-10-23 em 2 vCPU.

    duracao_bruto_s: duração do vídeo bruto inteiro (o que vai para o banco ou é transcrito).
    duracao_reel_s:  duração do Reel final (≈ trecho pré-cortado que passa por polish/director).
    banco=True: bruto entra no banco (ingestão completa) e o trecho é pré-cortado dele.
    banco=False: bruto curto vai direto (transcrição do bruto + normalização do bruto inteiro).
    Escala por CPU: (2 / cpus) ** 0.8 nas etapas de CPU (download não escala). Não inclui o tempo de
    escrever plano/roteiro nem a espera por aprovação do Gabriel; some 20–30 % de margem ao dar prazo.
    """
    cpus = cpus or effective_cpus()
    k = max(0.35, min(2.5, (2.0 / max(cpus, 0.5)) ** 0.8))
    b, r = float(duracao_bruto_s), float(duracao_reel_s)
    size = tamanho_bytes if tamanho_bytes else b * 2.0e6
    et = {}
    et["download Drive"] = (size / 100e6 / 60, size / 28e6 / 60)
    if banco:
        et["ingestão no banco (inclui transcrição)"] = (0.6 * b * k / 60, 0.8 * b * k / 60)
        et["pré-corte do trecho"] = (7.6 * r * 0.85 * k / 60, 7.6 * r * 1.2 * k / 60)
    else:
        et["transcrição do bruto"] = (0.45 * b * 0.85 * k / 60, 0.45 * b * 1.2 * k / 60)
        et["normalização/ingest do bruto"] = (7.6 * b * 0.85 * k / 60, 7.6 * b * 1.2 * k / 60)
    if polish:
        p = (30 * 60 / 55) * r * k / 60
        et["reel-polish completo (retoque ≈ 43 %)"] = (0.85 * p, 1.25 * p)
    d = 10 * (0.5 * r / 55 + 0.5 * n_graficos / 11) * k
    et[f"reel-director graphics->final ({n_graficos} gráficos)"] = (0.8 * d, 1.25 * d)
    tot = (sum(v[0] for v in et.values()), sum(v[1] for v in et.values()))
    return {"etapas": et, "total_min": tot, "prazo_sugerido_min": round(tot[1] * 1.25), "cpus": cpus, "fator_cpu": k}


def print_estimates(a):
    print("\nTEMPOS MEDIDOS (2026-10-23, 2 vCPU):")
    w = max(len(x) for x, _ in MEDIDAS)
    for x, y in MEDIDAS:
        print(f"  {x.ljust(w)}  {y}")
    bruto, reel = (a.estimar if a.estimar else (600, 55))
    e = estimar(bruto, reel, a.graficos, polish=not a.sem_polish, banco=not a.sem_banco)
    tag = "" if a.estimar else "  (exemplo; use --estimar <bruto_s> <reel_s>)"
    print(f"\nESTIMATIVA para bruto {bruto:g} s -> Reel {reel:g} s, {e['cpus']:g} vCPU (fator {fmt(e['fator_cpu'], 2)}){tag}:")
    w = max(len(x) for x in e["etapas"])
    for x, (lo, hi) in e["etapas"].items():
        print(f"  {x.ljust(w)}  {fmt(lo):>5}–{fmt(hi):>5} min")
    lo, hi = e["total_min"]
    print(f"  {'TOTAL de máquina'.ljust(w)}  {fmt(lo):>5}–{fmt(hi):>5} min  -> prazo a informar: ~{e['prazo_sugerido_min']} min "
          "(+ tempo de plano e aprovação)")


# ------------------------------------------------------------------ saída
def print_table():
    w1 = max(len(r["grupo"]) for r in ROWS)
    w2 = min(48, max(len(r["item"]) for r in ROWS))
    print(f"\n{'GRUPO'.ljust(w1)}  {'ITEM'.ljust(w2)}  {'STATUS':6}  DETALHE / AÇÃO")
    print("-" * (w1 + w2 + 90))
    for r in ROWS:
        item = r["item"] if len(r["item"]) <= w2 else r["item"][: w2 - 1] + "…"
        print(f"{r['grupo'].ljust(w1)}  {item.ljust(w2)}  {r['status']:6}  {r['detalhe']}")
        if r["status"] != OK and r["acao"]:
            print(f"{''.ljust(w1)}  {''.ljust(w2)}  {'':6}  -> {r['acao']}")
    n = {s: sum(r["status"] == s for r in ROWS) for s in (OK, AVISO, FALHA)}
    print("-" * (w1 + w2 + 90))
    print(f"Resumo: {n[OK]} OK, {n[AVISO]} AVISO, {n[FALHA]} FALHA")
    gab = [r for r in ROWS if r["gabriel"] and r["status"] != OK]
    if gab:
        print("\nAVISAR O GABRIEL AGORA (depende dele; o resto do trabalho segue em paralelo):")
        for r in gab:
            print(f"  - {r['item']}: {r['acao']}")
    if FIXES:
        print("\n--fix fez:")
        for f in FIXES:
            print(f"  - {f}")
    return n[FALHA]


def default_director(links_base: Path, root: Path) -> Path:
    if os.environ.get("REEL_DIRECTOR_DIR"):
        return Path(os.environ["REEL_DIRECTOR_DIR"])
    for c in (links_base / "reel-director-skill", links_base / "reel-director"):
        if (c / "scripts" / "run_all.py").exists():
            return c
    return root / "reel-director"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.split("\n\n")[1:]))
    ap.add_argument("--fix", action="store_true", help="corrige o que for seguro e local")
    ap.add_argument("--root", type=Path, default=HERE, help="clone do repositório editinstagraam (padrão: pasta deste script)")
    ap.add_argument("--director", type=Path, help="instalação da reel-director em uso (padrão: $REEL_DIRECTOR_DIR, "
                    "/home/claude/reel-director-skill, /home/claude/reel-director, ou <root>/reel-director)")
    ap.add_argument("--links-base", type=Path, default=Path("/home/claude"),
                    help="onde ficam reel-director, reel-director-skill, reel_voz (padrão: /home/claude)")
    ap.add_argument("--home", type=Path, default=Path.home(), help="onde fica ~/reel-polish-src (padrão: $HOME)")
    ap.add_argument("--probe-id", action="append", metavar="[pasta=]ID",
                    help="arquivo do Drive a testar (pasta = entrada|processado); repetível")
    ap.add_argument("--no-drive", action="store_true", help="não testa o Drive (sem rede)")
    ap.add_argument("--estimar", nargs=2, type=float, metavar=("BRUTO_S", "REEL_S"), help="estimativa para este caso")
    ap.add_argument("--graficos", type=int, default=11, help="nº de gráficos Remotion (padrão 11)")
    ap.add_argument("--sem-polish", action="store_true", help="estimativa sem reel-polish")
    ap.add_argument("--sem-banco", action="store_true", help="estimativa com bruto direto (sem ingestão no banco)")
    a = ap.parse_args()
    a.root = a.root.expanduser().absolute()
    a.links_base = a.links_base.expanduser().absolute()
    a.home = a.home.expanduser().absolute()
    a.director = (a.director.expanduser().absolute() if a.director else default_director(a.links_base, a.root))

    t0 = time.time()
    print(f"preflight {'--fix ' if a.fix else ''}| clone {a.root} | reel-director {a.director} | HOME {a.home}")
    check_machine(a)
    if check_repo(a):
        check_director(a)
        check_remotion(a)
        check_sisters(a)
        check_fastcloner(a)
        if a.no_drive:
            row("Drive (link)", "teste do Drive", AVISO, "pulado (--no-drive)", "rode sem --no-drive antes de baixar")
        else:
            check_drive(a)
        check_ffmpeg(a)
        check_models(a)
        check_python(a)
        check_whisper(a)
    nfail = print_table()
    print_estimates(a)
    print(f"\n(preflight em {time.time() - t0:.0f} s)")
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
