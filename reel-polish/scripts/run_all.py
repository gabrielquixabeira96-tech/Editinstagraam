#!/usr/bin/env python3
"""run_all — encadeia o pipeline do reel-polish a partir de polish.json, refazendo SÓ o necessário.

Etapas (mode raw):   analyze -> reframe -> stabilize -> grade -> beauty -> [retouch] -> audio -> render -> qa
Etapas (mode final): analyze ->                         grade -> beauty -> [retouch] -> audio -> render -> qa
(em mode final, grade lê o `source` diretamente; nada de recorte/estabilização. `retouch` só roda com
`retouch.enabled: true` — retoque avançado por MediaPipe: nariz, linha do cabelo, barba, pele.)

Cada etapa grava em state.json a assinatura das suas entradas (arquivos + campos do polish.json
de que depende + código do script); se nada mudou, é pulada. Ex.: trocar só `beauty_intensity`
refaz beauty -> render -> qa; trocar `look` refaz grade -> beauty -> render -> qa.

Uso:
  python scripts/run_all.py work/<nome>/polish/polish.json
  python scripts/run_all.py polish.json --from grade     # força a partir de uma etapa
  python scripts/run_all.py polish.json --force          # refaz tudo (menos a análise)
  python scripts/run_all.py polish.json --skip-qa
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402

HERE = Path(__file__).resolve().parent
ORDER = ["reframe", "stabilize", "grade", "beauty", "retouch", "audio", "render", "qa"]
SCRIPT = {"analyze": "01_analyze.py", "reframe": "02_reframe.py", "stabilize": "03_stabilize.py",
          "grade": "04_grade.py", "beauty": "05_beauty.py", "retouch": "05b_retouch.py", "audio": "06_audio_polish.py",
          "render": "07_render.py", "qa": "08_qa.py"}
OUTPUTS = {"reframe": ["reframe", "track"], "stabilize": ["stabilize"], "grade": ["grade"], "beauty": ["beauty"],
           "retouch": ["retouch"], "audio": ["audio"], "render": ["final"], "qa": ["qa"]}


def call(stage, args):
    cmd = [sys.executable, str(HERE / SCRIPT[stage])] + [str(a) for a in args]
    t0 = time.time()
    pc.log(f"\n▶ {stage}: {' '.join(Path(c).name if i < 2 else c for i, c in enumerate(cmd))}")
    r = subprocess.run(cmd)
    if r.returncode not in (0, 2) or (r.returncode == 2 and stage != "qa"):
        pc.die(f"etapa '{stage}' falhou (código {r.returncode})")
    pc.log(f"  ({time.time() - t0:.1f} s)")
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description="Pipeline completo do reel-polish com cache por etapa.")
    ap.add_argument("polish", help="polish.json (normalmente em work/<nome>/polish/polish.json)")
    ap.add_argument("--workdir", help="pasta de trabalho (padrão: pasta do polish.json)")
    ap.add_argument("--from", dest="from_stage", choices=ORDER, help="força refazer a partir desta etapa")
    ap.add_argument("--force", action="store_true", help="refaz todas as etapas (a análise é preservada)")
    ap.add_argument("--skip-qa", action="store_true")
    args = ap.parse_args()

    cfg_path = Path(args.polish).resolve()
    if not cfg_path.exists():
        pc.die(f"polish.json não encontrado: {cfg_path}")
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    src = pc.polish_source(cfg, cfg_path)
    look = pc.load_look(cfg.get("look"), cfg_path.parent)
    common = [cfg_path, "--workdir", workdir]

    if P["analysis"].exists():
        pc.log(f"✓ analyze: analysis.json existente reutilizado ({P['analysis']})")
    else:
        call("analyze", [src, "--workdir", workdir])

    raw = cfg["mode"] == "raw"
    order = ORDER if raw else [s for s in ORDER if s not in ("reframe", "stabilize")]
    retouch_on = bool(cfg["retouch"].get("enabled"))
    if not retouch_on:
        order = [s for s in order if s != "retouch"]
    code = {s: pc.file_sig(HERE / SCRIPT[s]) for s in SCRIPT}
    common_sig = pc.file_sig(HERE / "polish_common.py")
    src_sig = pc.file_sig(src)
    an_sig = pc.file_sig(P["analysis"])
    lut = cfg.get("lut") or look.get("lut")
    sig = {}
    sig["reframe"] = pc.sha({"src": src_sig, "an": an_sig, "framing": cfg["framing"], "framing_params": cfg.get("framing_params"), "fit": cfg["fit"],
                             "static": cfg["static"], "code": [code["reframe"], common_sig]})
    sig["stabilize"] = pc.sha({"prev": sig["reframe"], "stab": cfg["stabilize"], "code": code["stabilize"]})
    sig["grade"] = pc.sha({"prev": sig["stabilize"] if raw else src_sig, "an": an_sig, "mode": cfg["mode"],
                           "look": look, "lut": pc.file_sig(pc.resolve_path(lut, cfg_path.parent)) if lut else None,
                           "exposure": cfg["exposure"], "wb": cfg["white_balance"], "denoise": cfg["denoise"],
                           "sat": [cfg["saturation"], cfg["vibrance"], cfg["vignette"]], "code": code["grade"]})
    sig["beauty"] = pc.sha({"prev": sig["grade"], "i": cfg["beauty_intensity"], "opts": cfg["beauty"], "code": code["beauty"]})
    sig["retouch"] = pc.sha({"prev": sig["beauty"], "retouch": cfg["retouch"], "code": code["retouch"]})
    sig["audio"] = pc.sha({"src": src_sig, "an": an_sig, "audio": cfg["audio"], "code": code["audio"]})
    sig["render"] = pc.sha({"v": sig["retouch"] if retouch_on else sig["beauty"], "a": sig["audio"],
                            "sharpen": cfg["sharpen"], "fade": cfg["fade"], "code": code["render"]})
    sig["qa"] = pc.sha({"render": sig["render"], "code": code["qa"]})

    state = pc.load_json(P["state"]) if P["state"].exists() else {}
    forced = set()
    if args.force:
        forced = set(order)
    elif args.from_stage:
        forced = set(order[order.index(args.from_stage):]) if args.from_stage in order else set()
    qa_code = 0
    for stage in order:
        if stage == "qa" and args.skip_qa:
            continue
        outs_ok = all(P[o].exists() for o in OUTPUTS[stage])
        if stage not in forced and outs_ok and state.get(stage) == sig[stage]:
            pc.log(f"✓ {stage}: sem mudanças — pulada")
            continue
        c = call(stage, common)
        if stage == "qa":
            qa_code = c
        state[stage] = sig[stage]
        pc.save_json(P["state"], state)

    pc.log(f"\nFinal: {P['final']}")
    if P["qa"].exists() and not args.skip_qa:
        qa = pc.load_json(P["qa"])
        pc.log(f"QA: {qa['status'].upper()} — {P['qa']}")
        if qa["status"] != "aprovado":
            qa_code = 2
    sys.exit(qa_code)


if __name__ == "__main__":
    main()
