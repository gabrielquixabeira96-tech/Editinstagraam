#!/usr/bin/env python3
"""Testes unitários determinísticos da biblioteca de efeitos (scripts/vfx.py).

Cobre: easing, transições (cut/dissolve/match/luma/whip/zoom_through), zoom_frame, over_rgba,
key_green/refine_alpha/despill, color_match/light_wrap. Não precisa de GPU, Remotion nem rede.

Uso: python3 tests/test_vfx.py   (sai 0 se tudo passar)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vfx  # noqa: E402

W, H = vfx.W, vfx.H
fails = []


def ok(cond, msg):
    if not cond:
        fails.append(msg)
        print(f"  FALHOU: {msg}")
    else:
        print(f"  ok: {msg}")


def main():
    A = np.zeros((H, W, 3), np.uint8)       # preto
    B = np.full((H, W, 3), 255, np.uint8)   # branco

    # easing
    ok(abs(vfx.ease_out_cubic(0)) < 1e-6 and abs(vfx.ease_out_cubic(1) - 1) < 1e-6, "ease_out_cubic 0..1")
    ok(vfx.ease_in_out_cubic(0.5) == 0.5, "ease_in_out_cubic simétrico em 0,5")

    # transições: dissolve/match no meio ficam entre A e B
    for k in ("dissolve", "match"):
        mid = vfx.transition(k, A, B, 0.5)
        ok(mid.shape == (H, W, 3) and 40 < mid.mean() < 215, f"{k} mistura no meio")
    # luma: B com gradiente de luminância -> ao longo de p revela cada vez mais de B
    Bg = np.tile(np.linspace(0, 255, H, dtype=np.uint8), (W, 1)).T[..., None].repeat(3, 2)
    early = vfx.transition("luma", A, Bg, 0.25).mean()
    late = vfx.transition("luma", A, Bg, 0.75).mean()
    ok(late > early, "luma revela B progressivamente (p maior = mais B)")
    ok(vfx.transition("cut", A, B, 0.2).mean() == 255, "cut é instantâneo (mostra B)")
    ok(vfx.transition("whip", A, B, 0.5).shape == (H, W, 3), "whip retorna quadro")
    ok(vfx.transition("zoom_through", A, B, 0.5).shape == (H, W, 3), "zoom_through retorna quadro")

    # zoom_frame amplia (centro fica igual, bordas mudam)
    grad = np.tile(np.linspace(0, 255, W, dtype=np.uint8), (H, 1))[..., None].repeat(3, 2)
    z = vfx.zoom_frame(grad, 1.3, 0.5, 0.5)
    ok(z.shape == grad.shape and not np.array_equal(z, grad), "zoom_frame 1.3x altera o quadro")
    ok(np.array_equal(vfx.zoom_frame(grad, 1.0), grad), "zoom_frame 1.0x é identidade")

    # over_rgba: branco opaco cobre; alfa 0 não cobre
    base = A.copy()
    crop = np.full((100, 100, 4), 255, np.uint8)
    vfx.over_rgba(base, crop, (10, 10, 110, 110))
    ok(base[50, 50].mean() > 250 and base[300, 300].mean() == 0, "over_rgba cobre só a bbox")
    base2 = np.full((H, W, 3), 128, np.uint8)
    crop0 = np.dstack([np.full((50, 50, 3), 255, np.uint8), np.zeros((50, 50), np.uint8)])
    vfx.over_rgba(base2, crop0, (0, 0, 50, 50))
    ok(base2[25, 25].mean() == 128, "over_rgba com alfa 0 não altera")

    # chroma: fundo verde com um sujeito cinza no CENTRO (sem tocar as bordas, para o estimador de cor-chave)
    g = np.full((H, W, 3), (0, 255, 0), np.uint8)   # BGR verde
    g[H // 2 - 300: H // 2 + 300, W // 2 - 200: W // 2 + 200] = (120, 120, 120)  # sujeito cinza central
    a = vfx.key_green(g, 0.14, 0.06)
    ok(a[100, 100].mean() < 0.2 and a[H // 2, W // 2].mean() > 0.8, "key_green: verde->0, sujeito->1")
    ar = vfx.refine_alpha(a, 2, 3)
    ok(ar.shape == (H, W) and 0 <= ar.min() and ar.max() <= 1, "refine_alpha em [0,1]")
    d = vfx.despill(g)
    ok(d[:, 100, 1].mean() < g[:, 100, 1].mean(), "despill reduz o verde")

    # color_match/light_wrap não quebram forma
    fg = np.full((H, W, 3), (80, 120, 160), np.uint8)
    bg = np.full((H, W, 3), (40, 40, 40), np.uint8)
    al = np.ones((H, W), np.float32)
    ok(vfx.color_match(fg, bg, al, 0.5).shape == fg.shape, "color_match preserva forma")
    ok(vfx.light_wrap(fg, bg, al * 0.5, 0.35).shape == fg.shape, "light_wrap preserva forma")

    print(f"\n{'TODOS OK' if not fails else str(len(fails)) + ' FALHA(S)'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
