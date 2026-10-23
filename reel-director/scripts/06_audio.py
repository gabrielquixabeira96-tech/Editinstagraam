#!/usr/bin/env python3
"""06 — Áudio: voz do Gabriel + trilha (-18 dB, ducking) + SFX presos às transições -> mix.wav (-14 LUFS, TP -1).

A voz vem do cut.mkv (áudio original) ou já é a narração clonada (quando o ingest trocou a trilha). SFX:
  * cada transição whip/zoom_through recebe um `whoosh` no ponto da virada;
  * cada punch-in (plan.zoom) recebe um `pop` leve;
  * SFX explícitos do plano (plan.sfx) são somados.
Respeita o espaçamento mínimo entre SFX (styles.audio.sfx_min_gap_s). J/L-cut: o B-roll/`ai` são L-cuts por
construção (a voz contínua segue por baixo da cena), então o SFX da transição marca a virada sem cortar a fala;
para um J-cut explícito, adiante o início do shot no plano em relação à frase.

A mixagem em si (ducking por sidechain + loudnorm em 2 passadas) é feita pela reel-editor (05_audio), para não
duplicar a lógica de loudness. Este script só monta o audio_edl.json (sfx/music na timeline ORIGINAL) e a chama.

Uso: python scripts/06_audio.py plan.json
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    st = dc.load_style(plan.get("style", "director"))
    a = st["audio"]
    segs, _ = dc.timemap(W_)

    # SFX das transições e punch-ins (tempos na timeline ORIGINAL; a reel-editor remapeia)
    sfx = []
    gap = st["reach"]["sfx_min_gap_s"]
    for s in plan["shots"]:
        tr = s.get("in", "cut")
        name = st["transitions"].get(tr, {}).get("sfx")
        if name:
            sfx.append({"t": float(s["t"]), "name": name, "db": a["sfx_db"], "why": f"transição {tr}"})
    for z in plan["zoom"]:
        sfx.append({"t": float(z["t"]), "name": st["punch_in"]["sfx"], "db": a["sfx_db"] - 2, "why": "punch-in"})
    for extra in plan.get("sfx", []):
        sfx.append({"t": float(extra["t"]), "name": extra.get("name", "pop"),
                    "db": extra.get("db", a["sfx_db"]), "why": extra.get("why", "")})
    # espaçamento mínimo
    sfx.sort(key=lambda x: x["t"])
    spaced, last = [], -1e9
    for s in sfx:
        if s["t"] - last >= gap - 1e-6:
            spaced.append(s)
            last = s["t"]
        else:
            dc.log(f"  SFX em {s['t']:.2f}s suprimido (espaçamento < {gap}s)")

    # resolve arquivos de SFX/música para caminhos licenciados
    def resolve_name(n):
        p = a["sfx_library"].get(n, n)
        return dc.resolve(p, dc.SKILL)
    sfx_edl = []
    for s in spaced:
        f = resolve_name(s["name"])
        if not f.exists():
            dc.log(f"  aviso: SFX '{s['name']}' não encontrado ({f}) — ignorado")
            continue
        sfx_edl.append({"t": s["t"], "file": str(f), "db": s["db"]})

    music = plan.get("music") or a.get("music_default")
    music_p = dc.resolve(music, dc.SKILL) if music else None
    if music_p and not music_p.exists():
        dc.log(f"  aviso: trilha {music_p} não existe — sem música")
        music_p = None

    audio_edl = {
        "source": str(W_ / "cut.mkv"), "keep": plan["keep"], "padding_ms": int(plan.get("padding_ms", 80)),
        "style": str(dc.EDITOR / "styles" / "default.json"),
        "music": str(music_p) if music_p else None, "music_db": a["music_db"],
        "sfx": sfx_edl,
        "audio": {"duck_db": a["duck_db"], "sfx_db": a["sfx_db"],
                  "target_lufs": a["target_lufs"], "target_tp": a["target_tp"], "voice_compress": True},
    }
    dc.save_json(W_ / "audio_edl.json", audio_edl)
    dc.log(f"áudio: {len(sfx_edl)} SFX, música={'sim' if music_p else 'não'} -> delega mix à reel-editor")

    # licença: registra a trilha/SFX desta skill para o 05_audio não recusar
    r = subprocess.run(["python3", str(dc.EDITOR / "scripts" / "05_audio.py"), str(W_ / "audio_edl.json"),
                        "--workdir", str(W_), "--video", str(W_ / "cut.mkv"), "--out", str(W_ / "mix.wav")],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    for ln in out.splitlines():
        if ln.strip():
            dc.log("  " + ln)
    if r.returncode != 0:
        dc.die(f"05_audio (reel-editor) falhou:\n{out[-2000:]}")
    dc.log(f"OK: {W_/'mix.wav'}")


if __name__ == "__main__":
    main()
