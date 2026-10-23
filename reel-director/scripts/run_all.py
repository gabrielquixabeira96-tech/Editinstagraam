#!/usr/bin/env python3
"""run_all — pipeline completo do reel-director, com cache por etapa.

Etapas: ingest -> transcribe -> direct -> [polish] -> cut -> graphics -> compose -> subtitles -> audio -> render -> qa -> post.
O Claude DECIDE (plan.json), os scripts EXECUTAM. Para antes do render se o plano não estiver aprovado
(`plan.approved: true` ou --approve-plan), para o Gabriel conferir o plano/safe area.

Uso:
  python scripts/run_all.py plan.json                 # até 'direct' (imprime o plano e para)
  python scripts/run_all.py plan.json --approve-plan  # roda tudo
  python scripts/run_all.py plan.json --from compose  # recomeça de uma etapa (invalida o cache dela em diante)
  python scripts/run_all.py plan.json --no-polish --no-ai
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402

HERE = Path(__file__).resolve().parent
ORDER = ["ingest", "transcribe", "direct", "polish", "cut", "graphics", "compose", "subtitles", "audio", "render", "qa", "post"]


def sh(cmd):
    dc.log("$ " + " ".join(str(c) for c in cmd[-3:]))
    r = subprocess.run([str(c) for c in cmd])
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--approve-plan", action="store_true")
    ap.add_argument("--from", dest="from_stage", choices=ORDER)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-polish", action="store_true")
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args()
    plan_path = str(Path(args.plan).resolve())
    plan = dc.load_plan(plan_path)
    W_ = dc.workdir_for(plan)
    W_.mkdir(parents=True, exist_ok=True)
    stt = dc.State(W_)
    if args.force:
        stt.d = {}
    if args.from_stage:
        stt.clear_from(ORDER, args.from_stage)

    ed = str(dc.EDITOR / "scripts")
    approved = args.approve_plan or plan.get("approved")
    do_polish = plan.get("polish", {}).get("enabled") and not args.no_polish

    def need(stage, sig, outs):
        return not stt.fresh(stage, sig, [W_ / o for o in outs])

    def done(stage, sig):
        stt.mark(stage, sig)

    # 1 ingest
    sig = dc.sha([dc.fsig(dc.resolve(plan["source"], plan["_dir"])), dc.code_sig("01_ingest.py"),
                  plan.get("narration")])
    if need("ingest", sig, ["ingest.mp4", "ingest.json"]):
        if sh(["python3", HERE / "01_ingest.py", plan_path]):
            dc.die("ingest falhou")
    done("ingest", sig)

    # 2 transcribe (reel-editor; nunca retranscreve se existir)
    if not (W_ / "transcript.json").exists():
        if sh(["python3", f"{ed}/01_transcribe.py", W_ / "ingest.mp4", "--workdir", W_]):
            dc.die("transcribe falhou")

    # 3 direct
    sig = dc.sha([dc.fsig(plan_path), dc.code_sig("02_direct.py"), dc.fsig(W_ / "ingest.json")])
    if need("direct", sig, ["edl.json", "plano.txt"]):
        if sh(["python3", HERE / "02_direct.py", plan_path]):
            dc.die("direct falhou")
    done("direct", sig)

    if not approved:
        dc.log("\n--- plano NÃO aprovado. Confira " + str(W_ / "plano.txt") +
               " e rode de novo com --approve-plan (ou plan.approved=true). ---")
        return

    # 4 polish (opcional)
    source_for_cut = W_ / "ingest.mp4"
    if do_polish:
        pdir = dc.polish_dir()
        if not pdir:
            dc.die("reel-polish não encontrado. Clone o repo (ver SKILL.md) ou use --no-polish.")
        polish_json = W_ / "polish" / "polish.json"
        polish_json.parent.mkdir(parents=True, exist_ok=True)
        pj = plan.get("polish", {})
        dc.save_json(polish_json, {"source": "../ingest.mp4", "mode": "raw", "framing": pj.get("framing", "medium"),
                                   "fit": "auto", "look": pj.get("look", "looks/natural.json"),
                                   "beauty_intensity": pj.get("beauty_intensity", 30),
                                   "retouch": pj.get("retouch", {"preset": "gabriel"}),
                                   "fade": {"in_s": 0, "out_s": 0}})
        sig = dc.sha([dc.fsig(W_ / "ingest.mp4"), dc.fsig(polish_json)])
        if need("polish", sig, ["polish/polished.mp4"]):
            if sh(["python3", f"{pdir}/scripts/run_all.py", polish_json, "--workdir", W_ / "polish", "--skip-qa"]):
                dc.die("polish falhou")
        done("polish", sig)
        source_for_cut = W_ / "polish" / "polished.mp4"
        # reaponta a edl para o polido
        edl = dc.load_json(W_ / "edl.json")
        edl["source"] = str(source_for_cut)
        dc.save_json(W_ / "edl.json", edl)

    # 5 cut (reel-editor)
    sig = dc.sha([dc.fsig(W_ / "edl.json"), dc.fsig(source_for_cut), dc.fsig(W_ / "transcript.json")])
    if need("cut", sig, ["cut.mkv", "timemap.json"]):
        if sh(["python3", f"{ed}/02_apply_edl.py", W_ / "edl.json", "--workdir", W_]):
            dc.die("cut falhou")
    done("cut", sig)

    # 6 graphics
    sig = dc.sha([dc.fsig(plan_path), dc.fsig(W_ / "timemap.json"), dc.code_sig("04_graphics.py"),
                  dc.fsig(dc.REMOTION / "src" / "index.jsx")])
    if need("graphics", sig, ["graphics_manifest.json"]):
        if sh(["python3", HERE / "04_graphics.py", plan_path]):
            dc.die("graphics falhou")
    done("graphics", sig)

    # 7 compose
    sig = dc.sha([dc.fsig(plan_path), dc.fsig(W_ / "cut.mkv"), dc.fsig(W_ / "graphics_manifest.json"),
                  dc.code_sig("05_compose.py", "vfx.py")])
    if need("compose", sig, ["director.mkv"]):
        if sh(["python3", HERE / "05_compose.py", plan_path]):
            dc.die("compose falhou")
    done("compose", sig)

    # 8 subtitles (reel-editor, por cima do director.mkv)
    sig = dc.sha([dc.fsig(W_ / "edl.json"), dc.fsig(W_ / "director.mkv")])
    if need("subtitles", sig, ["subs.mkv"]):
        if sh(["python3", f"{ed}/03_subtitles.py", W_ / "edl.json", "--workdir", W_,
               "--video", W_ / "director.mkv", "--out", W_ / "subs.mkv"]):
            dc.die("subtitles falhou")
    done("subtitles", sig)

    # 9 audio
    sig = dc.sha([dc.fsig(plan_path), dc.fsig(W_ / "cut.mkv"), dc.code_sig("06_audio.py")])
    if need("audio", sig, ["mix.wav"]):
        if sh(["python3", HERE / "06_audio.py", plan_path]):
            dc.die("audio falhou")
    done("audio", sig)

    # 10 render (reel-editor)
    sig = dc.sha([dc.fsig(W_ / "subs.mkv"), dc.fsig(W_ / "mix.wav"), dc.fsig(W_ / "timemap.json")])
    if need("render", sig, ["final.mp4"]):
        if sh(["python3", f"{ed}/06_render.py", W_ / "edl.json", "--workdir", W_,
               "--video", W_ / "subs.mkv", "--audio", W_ / "mix.wav", "--out", W_ / "final.mp4"]):
            dc.die("render falhou")
    done("render", sig)

    # 11 qa
    rc = sh(["python3", HERE / "07_qa.py", plan_path])
    qa = dc.load_json(W_ / "qa_report.json")
    if rc != 0:
        dc.log(f"\n*** QA REPROVADO: {qa['defeitos']} ***")
        dc.log("Corrija o plano e rode --from graphics (ou a etapa afetada). Nada é entregue reprovado.")
        sys.exit(3)

    # 12 post
    if sh(["python3", HERE / "08_post.py", plan_path]):
        dc.die("post falhou")

    dc.log(f"\n=== PRONTO ===\nvídeo: {W_/'final.mp4'}\ncapa:  {W_/'capa_4x5.jpg'}\n"
           f"post:  {W_/'post.txt'}\nQA:    {qa['veredito']}  {qa['checagens']}")


if __name__ == "__main__":
    main()
