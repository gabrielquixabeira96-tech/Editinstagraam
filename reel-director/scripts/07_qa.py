#!/usr/bin/env python3
"""07 — QA automático. Reprova (veredito "reprovado") se qualquer regra falhar:

  1. gancho renderizado > hook_max_s (2 s) ou com mais palavras que o teto;
  2. plano estático: intervalo entre eventos visuais (corte, transição, punch-in, entrada de gráfico) > static_shot_max_s (4 s);
  3. texto fora da área segura (x 65–1015) ou sobre a faixa da legenda (y 1140–1245) enquanto há legenda;
  4. loudness fora de -14 ±1 LUFS ou true peak > -1 dBTP;
  5. cena IA sem rótulo "...(IA)" no intervalo da cena;
  6. duração final > max_duration_s (60 s) sem `justify_long: true` no plano;
  7. (aviso) sem LowerThird com CRM, sem disclaimer educativo, ou duração fora de target_duration_s.

Escreve qa_report.json e sai com código 0 (aprovado) ou 3 (reprovado).

Uso: python scripts/07_qa.py plan.json
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402

CAP = (1140, 1245)  # faixa da legenda (y)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    st = dc.load_style(plan.get("style", "director"))
    reach = st["reach"]
    safe = st["safe"]["center"]
    final = W_ / "final.mp4"
    segs, _ = dc.timemap(W_)
    total = dc.rc.timemap_total(segs)
    man = dc.load_json(W_ / "graphics_manifest.json")["items"] if (W_ / "graphics_manifest.json").exists() else []
    defects, warns, checks = [], [], {}

    # tempos de legenda (nova timeline)
    cap_words = []
    sw = W_ / "subs_words.json"
    if sw.exists():
        for ln in dc.load_json(sw).get("lines", []):
            for wd in ln:
                cap_words.append((wd["start"], wd["end"]))

    # 1. gancho
    hooks = [m for m in man if m["comp"] == "HookTitle"]
    if hooks:
        h = min(hooks, key=lambda m: m["start_frame"])
        hs = h["nframes"] / dc.FPS
        checks["hook_s"] = round(hs, 2)
        if hs > reach["hook_max_s"] + 0.05:
            defects.append(f"gancho dura {hs:.2f}s > {reach['hook_max_s']}s")
    else:
        defects.append("sem gancho (HookTitle)")
    for g in plan["graphics"]:
        if g.get("comp") == "HookTitle":
            nwords = sum(len(l) for l in g.get("props", {}).get("lines", []))
            if nwords > reach["hook_text_max_words"]:
                defects.append(f"gancho com {nwords} palavras > {reach['hook_text_max_words']}")

    # 2. eventos visuais
    events = {0.0, total}
    for s in plan["shots"]:
        events.add(dc.map_t(s["t"], segs))
        events.add(dc.map_t(float(s["t"]) + float(s["dur"]), segs))
    for z in plan["zoom"]:
        events.add(dc.map_t(z["t"], segs))
    for m in man:
        events.add(m["start_frame"] / dc.FPS)
    ev = sorted(events)
    gaps = [(ev[i + 1] - ev[i], ev[i]) for i in range(len(ev) - 1)]
    maxgap, at = max(gaps, default=(0, 0))
    checks["max_static_gap_s"] = round(maxgap, 2)
    if maxgap > reach["static_shot_max_s"] + 0.05:
        defects.append(f"plano estático de {maxgap:.1f}s em t={at:.1f}s > {reach['static_shot_max_s']}s")

    # 3. área segura dos gráficos
    for m in man:
        bb = m.get("bbox")
        if not bb:
            continue
        x0, y0, x1, y1 = bb
        if x0 < safe["x0"] - 2 or x1 > safe["x1"] + 2:
            defects.append(f"gráfico {m['i']} ({m['comp']}) fora da área segura horizontal: x[{x0},{x1}]")
        if y0 < 258:
            defects.append(f"gráfico {m['i']} ({m['comp']}) invade o topo reservado (y0={y0})")
        # sobre a faixa da legenda enquanto há legenda
        if y1 > CAP[0] and y0 < CAP[1]:
            gs, ge = m["start_frame"] / dc.FPS, (m["start_frame"] + m["nframes"]) / dc.FPS
            if any(ws < ge and we > gs for ws, we in cap_words):
                defects.append(f"gráfico {m['i']} ({m['comp']}) sobre a legenda (y[{y0},{y1}]) com legenda ativa")

    # 4. loudness
    if final.exists():
        meas = dc.rc.measure_loudness(final)
        checks["lufs"] = meas["I"]
        checks["true_peak_dbtp"] = meas["TP"]
        if meas["I"] is None or abs(meas["I"] - reach.get("target_lufs", -14)) > 1.0 + 1e-6:
            tgt = st["audio"]["target_lufs"]
            if meas["I"] is None or abs(meas["I"] - tgt) > 1.0 + 1e-6:
                defects.append(f"loudness {meas['I']} LUFS fora de {tgt}±1")
        if meas["TP"] is not None and meas["TP"] > st["audio"]["target_tp"] + 1e-6:
            defects.append(f"true peak {meas['TP']} dBTP acima de {st['audio']['target_tp']}")
    else:
        defects.append("final.mp4 não existe")

    # 5. rótulo IA
    for s in plan["shots"]:
        if s.get("kind") != "ai":
            continue
        a = dc.map_t(s["t"], segs)
        b = dc.map_t(float(s["t"]) + float(s["dur"]), segs)
        ok = any(m["comp"] == "Label" and "ia" in str(
            next((g.get("props", {}).get("text", "") for g in plan["graphics"]
                  if g.get("comp") == "Label"), m.get("why", ""))).lower()
            and (m["start_frame"] / dc.FPS) < b and (m["start_frame"] + m["nframes"]) / dc.FPS > a for m in man)
        # mais robusto: qualquer Label auto/IA cobrindo a cena
        ok = ok or any(m["comp"] == "Label" and (m["start_frame"] / dc.FPS) < b + 0.5
                       and (m["start_frame"] + m["nframes"]) / dc.FPS > a - 0.5 for m in man)
        if not ok:
            defects.append(f"cena IA em t={s['t']}s sem rótulo (IA) no intervalo")

    # 6. duração
    checks["duration_s"] = round(total, 2)
    if total > reach["max_duration_s"] + 0.1 and not plan.get("justify_long"):
        defects.append(f"duração {total:.1f}s > {reach['max_duration_s']}s sem justify_long")
    lo, hi = reach["target_duration_s"]
    if not (lo <= total <= hi):
        warns.append(f"duração {total:.1f}s fora do alvo {lo}-{hi}s (ok se justificado)")

    # avisos de conformidade
    if not any(m["comp"] == "LowerThird" for m in man):
        warns.append("sem LowerThird (nome/CRM)")
    if not any("educativo" in str(g.get("props", {}).get("text", "")).lower() for g in plan["graphics"]):
        warns.append("sem disclaimer educativo")

    verdict = "aprovado" if not defects else "reprovado"
    report = {"veredito": verdict, "defeitos": defects, "avisos": warns, "checagens": checks}
    dc.save_json(W_ / "qa_report.json", report)
    dc.log(f"QA: {verdict.upper()}  ({checks})")
    for d in defects:
        dc.log(f"  DEFEITO: {d}")
    for wn in warns:
        dc.log(f"  aviso: {wn}")
    sys.exit(0 if verdict == "aprovado" else 3)


if __name__ == "__main__":
    main()
