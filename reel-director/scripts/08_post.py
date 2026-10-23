#!/usr/bin/env python3
"""08 — Capa 4:5 + texto do post. Gera o pacote de publicação a partir do final.mp4 e do plan.post.

  * capa_4x5.jpg (1080x1350, grade do feed): um quadro forte do vídeo (o 1º quadro com o gancho visível, ou
    plan.cover.t) com o título do gancho por cima, na identidade (Poppins, acento #39E58C).
  * post.txt: legenda do post. Regras: sério e técnico, SEM emoji (preferência do usuário), com CTA de ENVIO no fim
    ("Manda para quem..."), fecho educativo e a assinatura CRM. Hashtags sóbrias (plan.post.hashtags).

NÃO inventa a legenda: usa plan.post.caption (o Claude escreve). Se faltar, monta uma base a partir do gancho e
AVISA que precisa de revisão. CTA sem verbo de envio -> aviso (R33: pedir reenvio, não só curtida).

Uso: python scripts/08_post.py plan.json
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402

EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⌀-⏿]")


def hook_text(plan):
    for g in plan["graphics"]:
        if g.get("comp") == "HookTitle":
            lines = g.get("props", {}).get("lines", [])
            return " ".join(w.replace("*", "") for ln in lines for w in ln)
    return ""


def make_cover(plan, W_, st):
    import cv2
    import numpy as np
    final = W_ / "final.mp4"
    t = float(plan.get("cover", {}).get("t", 1.0))
    import subprocess
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(final), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
    frame = np.frombuffer(p.stdout, np.uint8)[: 1080 * 1920 * 3].reshape(1920, 1080, 3).copy()
    # 4:5 = 1080x1350: recorta o centro vertical (y 285..1635)
    cov = frame[285:1635].copy()
    title = (plan.get("cover", {}).get("title") or hook_text(plan)).upper()
    # caixa escura + título
    overlay = cov.copy()
    cv2.rectangle(overlay, (0, 1010), (1080, 1350), (18, 12, 8), -1)
    cov = cv2.addWeighted(overlay, 0.55, cov, 0.45, 0)
    cv2.rectangle(cov, (65, 1040), (85, 1300), (140, 229, 57), -1)  # barra de acento #39E58C (BGR)
    y = 1110
    for line in _wrap(title, 18):
        cv2.putText(cov, line, (110, y), cv2.FONT_HERSHEY_DUPLEX, 2.4, (255, 255, 255), 5, cv2.LINE_AA)
        y += 90
    out = W_ / "capa_4x5.jpg"
    cv2.imwrite(str(out), cov, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return out


def _wrap(text, n):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= n:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    st = dc.load_style(plan.get("style", "director"))
    post = plan.get("post", {})
    warns = []

    cover = make_cover(plan, W_, st)

    caption = (post.get("caption") or "").strip()
    cta = (post.get("cta") or "").strip()
    if not caption:
        caption = f"{hook_text(plan)}.\n\n[rascunho — reescreva a legenda]"
        warns.append("plan.post.caption vazio: legenda base gerada do gancho; revise")
    if EMOJI.search(caption) or EMOJI.search(cta):
        warns.append("legenda/CTA com emoji — remover (preferência do usuário)")
        caption = EMOJI.sub("", caption)
        cta = EMOJI.sub("", cta)
    if cta and not re.search(st["reach"]["cta_send_regex"], cta.lower()):
        warns.append("CTA sem verbo de envio (manda/envia/compartilha) — R33 pede reenvio")
    if not cta:
        cta = "Manda este Reel para alguém que precisa ver isso."
        warns.append("plan.post.cta vazio: CTA de envio padrão inserido")

    tags = post.get("hashtags") or []
    sig = f"Dr. Gabriel Quixabeira · CRM-MT {st['reach']['lower_third']['sub'].split()[-1]}"
    body = (caption.rstrip() + "\n\n" + cta.strip() + "\n\n"
            + "Conteúdo educativo. Não substitui avaliação médica.\n" + sig)
    if tags:
        body += "\n\n" + " ".join(t if t.startswith("#") else "#" + t for t in tags)
    if EMOJI.search(body):
        body = EMOJI.sub("", body)
    (W_ / "post.txt").write_text(body + "\n", encoding="utf-8")

    dc.save_json(W_ / "post_meta.json", {"cover": str(cover), "post_txt": str(W_ / "post.txt"),
                                         "has_cta_send": bool(re.search(st["reach"]["cta_send_regex"], cta.lower())),
                                         "avisos": warns})
    dc.log(f"OK: capa {cover.name} + {W_/'post.txt'}")
    for wn in warns:
        dc.log(f"  aviso: {wn}")


if __name__ == "__main__":
    main()
