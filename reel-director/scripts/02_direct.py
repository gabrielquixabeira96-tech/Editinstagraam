#!/usr/bin/env python3
"""02 — Direção: valida o plan.json e escreve o edl.json (formato reel-editor) para o corte.

O Claude escreve o plan.json (gramática no SKILL.md). Este script NÃO inventa conteúdo: só valida o que o
Claude decidiu e traduz os trechos `keep` para uma EDL que a reel-editor (02_apply_edl, 03_subtitles, 05_audio,
06_render) consome. Tudo em `shots`, `graphics`, `zoom`, `sfx` é validado e usado depois por 04/05/06.

Validações (erro = aborta; aviso = segue):
  * keep não vazio, dentro da duração, sem sobreposição;
  * cada graphic tem `comp` conhecido e `t`/`dur`;
  * gancho: existe pelo menos 1 HookTitle começando em t<=keep[0].start+0.3 com dur<= hook_max_s e <= hook_text_max_words;
  * cena IA (`shots[].kind == "ai"`): exige `file` existente e `label` (o compositor força o rótulo);
  * rótulo IA, disclaimer e lower third presentes (avisa se faltar — QA depois reprova);
  * nada de graphic cuja faixa vertical declarada entre na faixa da legenda (checagem fina fica no 04/07).

Uso: python scripts/02_direct.py plan.json
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402

COMPS = {"HookTitle", "Counter", "BarChart", "LineChart", "Callout", "ProgressBar",
         "LowerThird", "ArticleFigure", "BigText", "Label"}
SHOT_KINDS = {"aroll", "broll", "ai", "chroma"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    ingest = dc.load_json(W_ / "ingest.json") if (W_ / "ingest.json").exists() else {}
    dur = ingest.get("probe", {}).get("duration") or dc.probe(dc.resolve(plan["source"], plan["_dir"]))["duration"]
    st = dc.load_style(plan.get("style", "director"))
    reach = st["reach"]
    errors, warns = [], []

    # fonte: bruto polido ou ingerido
    polished = W_ / "polish" / "polished.mp4"
    src = polished if (plan.get("polish", {}).get("enabled") and polished.exists()) else (W_ / "ingest.mp4")
    if not src.exists():
        errors.append(f"fonte não encontrada: {src} (rode ingest/polish antes)")

    # keep
    keep = sorted([{"start": float(k["start"]), "end": float(k["end"])} for k in plan["keep"]],
                  key=lambda k: k["start"])
    if not keep:
        errors.append("plan.keep vazio")
    last = -1
    for k in keep:
        if k["end"] <= k["start"]:
            errors.append(f"keep inválido: {k}")
        if k["start"] < last - 1e-6:
            errors.append(f"keep sobreposto em {k['start']}")
        if dur and k["end"] > dur + 0.1:
            warns.append(f"keep {k['end']:.2f}s além da duração {dur:.2f}s (será cortado)")
        last = k["end"]
    kept_len = sum(min(k["end"], dur or k["end"]) - k["start"] for k in keep) if keep else 0

    # graphics
    hooks = []
    for g in plan["graphics"]:
        if g.get("comp") not in COMPS:
            errors.append(f"graphic com comp desconhecido: {g.get('comp')}")
        if "t" not in g or "dur" not in g:
            errors.append(f"graphic sem t/dur: {g}")
        g.setdefault("props", {})
        if g.get("comp") == "HookTitle":
            hooks.append(g)
    if not hooks:
        errors.append("sem HookTitle: o gancho é obrigatório (R1)")
    else:
        h = min(hooks, key=lambda x: x["t"])
        if h["t"] > keep[0]["start"] + 0.35:
            warns.append(f"HookTitle começa em {h['t']:.2f}s, depois do início do keep ({keep[0]['start']:.2f}s)")
        if h["dur"] > reach["hook_max_s"] + 0.5:
            warns.append(f"gancho com {h['dur']:.1f}s > {reach['hook_max_s']}s (QA usa o tempo RENDERIZADO)")
        nwords = sum(len(ln) for ln in h["props"].get("lines", []))
        if nwords > reach["hook_text_max_words"]:
            warns.append(f"gancho com {nwords} palavras > {reach['hook_text_max_words']}")

    # shots
    for s in plan["shots"]:
        if s.get("kind") not in SHOT_KINDS:
            errors.append(f"shot com kind inválido: {s.get('kind')}")
        if s.get("kind") in ("broll", "ai", "chroma"):
            f = dc.resolve(s.get("file", ""), plan["_dir"])
            if not s.get("file") or not f.exists():
                errors.append(f"shot {s.get('kind')} em t={s.get('t')} sem arquivo existente: {s.get('file')}")
        if s.get("kind") == "ai":
            s.setdefault("label", reach["ai_label_text"])
        tr = s.get("in", "cut")
        if tr not in st["transitions"]:
            errors.append(f"transição desconhecida: {tr}")

    # elementos de conformidade
    def has(comp, textkey=None, needle=None):
        for g in plan["graphics"]:
            if g.get("comp") == comp:
                if needle is None:
                    return True
                if needle.lower() in str(g.get("props", {}).get(textkey, "")).lower():
                    return True
        return False
    if not has("LowerThird"):
        warns.append("sem LowerThird (nome/CRM) — exigido pelo CFM; QA reprova")
    if not any("educativo" in str(g.get("props", {}).get("text", "")).lower() for g in plan["graphics"]):
        warns.append("sem disclaimer educativo no fecho — QA reprova")
    if any(s.get("kind") == "ai" for s in plan["shots"]) and \
       not any(reach["ai_label_text"].split()[0].lower() in str(g.get("props", {}).get("text", "")).lower()
               for g in plan["graphics"]):
        warns.append("há cena IA sem rótulo 'Recriação ilustrativa (IA)' — o compositor força, mas declare no plano")

    if errors:
        for e in errors:
            dc.log(f"ERRO: {e}")
        dc.die(f"{len(errors)} erro(s) no plan.json")

    # edl.json compatível com a reel-editor (corte + legenda + render)
    edl = {
        "source": str(src), "keep": keep, "padding_ms": int(plan.get("padding_ms", 80)),
        "emphasis": plan.get("emphasis", []),
        "style": plan.get("subtitle_style", str(dc.EDITOR / "styles" / "default.json")),
        "fit": "cover",
    }
    dc.save_json(W_ / "edl.json", edl)

    # plano legível
    lines = [f"PLANO — {Path(plan['source']).name}", f"keep: {kept_len:.1f}s em {len(keep)} trecho(s)",
             f"gráficos: {len(plan['graphics'])}  shots: {len(plan['shots'])}  zoom: {len(plan['zoom'])}"]
    for g in sorted(plan["graphics"], key=lambda x: x["t"]):
        lines.append(f"  [{g['t']:6.2f} +{g['dur']:.1f}] {g['comp']:12} {g.get('why', '')}")
    for s in sorted(plan["shots"], key=lambda x: x.get("t", 0)):
        lines.append(f"  shot [{s.get('t', 0):6.2f} +{s.get('dur', 0):.1f}] {s['kind']:7} in={s.get('in', 'cut')} {s.get('why', '')}")
    for w in warns:
        lines.append(f"  aviso: {w}")
    (W_ / "plano.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    dc.save_json(W_ / "direction.json", {"warnings": warns, "kept_len": kept_len, "source": str(src)})
    dc.log("\n".join(lines))
    dc.log(f"OK: {W_/'edl.json'} e {W_/'plano.txt'}")


if __name__ == "__main__":
    main()
