#!/usr/bin/env python3
"""05 — Composição: cola a base (cut.mkv) com shots, transições, punch-in, chroma e gráficos -> director.mkv.

Passo 1 (base): percorre a NOVA timeline quadro a quadro. A base é o cut.mkv (A-roll); onde há um shot
`broll`/`ai`, a base é substituída pela cena em tela cheia (cover, em loop). Nas junções entre shots aplica a
transição declarada (vfx.transition: cut, dissolve, match, luma, whip, zoom_through). Punch-in (plan.zoom)
é aplicado só sobre o A-roll. Resultado: director_base.mkv.

Passo 2 (overlays): lê director_base.mkv e sobrepõe, quadro a quadro:
  * chroma: shots `kind=="chroma"` — recorte do clipe verde (vfx.key_green+despill+refine+light_wrap+color_match)
    sobre a base, na posição/escala do shot. Sem fundo verde local, use Runway remove_video_background (degrau 3).
  * gráficos: sequências PNG RGBA do 04 (vfx.over_rgba). `behind=true` recoloca a pessoa (MediaPipe) por cima
    do gráfico (texto atrás do apresentador).
Tudo é determinístico; a duração é preservada (nunca insere tempo). Tempos no plano estão na timeline ORIGINAL.

Uso: python scripts/05_compose.py plan.json
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_common as dc  # noqa: E402
import vfx  # noqa: E402

FPS = dc.FPS


def build_segments(plan, segs, total_f):
    """Lista de segmentos da base na nova timeline (quadros): aroll (cut) ou full-frame (broll/ai)."""
    raw = []
    for s in plan["shots"]:
        if s.get("kind") == "chroma":
            continue  # chroma é overlay (passo 2), não substitui a base
        a = dc.nframes(dc.map_t(s["t"], segs))
        b = dc.nframes(dc.map_t(float(s["t"]) + float(s["dur"]), segs))
        raw.append({"a": max(0, a), "b": min(total_f, max(a + 1, b)), "kind": s["kind"],
                    "file": str(dc.resolve(s["file"], plan["_dir"])) if s.get("file") else None,
                    "trans": s.get("in", "cut")})
    raw.sort(key=lambda x: x["a"])
    # preenche lacunas com aroll
    out, cur = [], 0
    for r in raw:
        if r["a"] > cur:
            out.append({"a": cur, "b": r["a"], "kind": "aroll", "file": None, "trans": "cut"})
        out.append(r)
        cur = r["b"]
    if cur < total_f:
        out.append({"a": cur, "b": total_f, "kind": "aroll", "file": None, "trans": "cut"})
    return out


def zoom_windows(plan, segs, st):
    wins = []
    pi = st["punch_in"]
    r = 0.25
    for z in plan["zoom"]:
        c = dc.map_t(z["t"], segs)
        scale = float(z.get("scale", pi["scale"]))
        fy = float(z.get("focus_y", pi["focus_y"]))
        wins.append((dc.nframes(c - r), dc.nframes(c), dc.nframes(c + r), dc.nframes(c + 1.0), scale, fy))
    return wins


def zoom_at(f, wins):
    for a, pk, hold_end, end, scale, fy in wins:
        if a <= f < end:
            if f < pk:
                p = vfx.ease_in_out_cubic((f - a) / max(1, pk - a))
            elif f < hold_end:
                p = 1.0
            else:
                p = 1 - vfx.ease_in_out_cubic((f - hold_end) / max(1, end - hold_end))
            return 1.0 + (scale - 1.0) * p, fy
    return None


class ClipReader:
    """Lê um clipe externo em cover 1080x1920, em loop, segurando o último quadro no fim."""

    def __init__(self, path):
        self.path = path
        self.r = vfx.Reader(path)
        self.buf = []
        self.i = 0

    def read(self):
        f = self.r.read()
        if f is None:  # fim -> reinicia (loop) se tivermos buffer, senão segura
            if self.buf:
                self.i = (self.i + 1) % len(self.buf)
                return self.buf[self.i]
            return None
        if len(self.buf) < 240:
            self.buf.append(f)
        return f

    def close(self):
        self.r.close()


def pass_base(plan, W_, segs, st):
    total_f = dc.rc.timemap_total(segs)
    total_f = dc.nframes(total_f)
    seglist = build_segments(plan, segs, total_f)
    wins = zoom_windows(plan, segs, st)
    tr = st["transitions"]
    cut = vfx.Reader(W_ / "cut.mkv")
    out = W_ / "director_base.mkv"
    w = vfx.Writer(out, crf=st["render"]["crf_intermediate"], preset=st["render"]["preset_intermediate"])

    readers = {}  # índice do segmento -> ClipReader (para broll/ai)

    def seg_at(f):
        for i, s in enumerate(seglist):
            if s["a"] <= f < s["b"]:
                return i, s
        return len(seglist) - 1, seglist[-1]

    def source_frame(i, s, cutframe):
        if s["kind"] == "aroll":
            return cutframe
        if i not in readers:
            readers[i] = ClipReader(s["file"])
        fr = readers[i].read()
        return fr if fr is not None else cutframe

    dc.log(f"compose base: {total_f} quadros, {len(seglist)} segmento(s)")
    for f in range(total_f):
        cutframe = cut.read()
        if cutframe is None:
            cutframe = np.zeros((vfx.H, vfx.W, 3), np.uint8)
        i, s = seg_at(f)
        base = source_frame(i, s, cutframe)
        # transição no início do segmento i (se não for o primeiro e não for corte)
        d = 0.0
        if i > 0 and s["trans"] not in ("cut", None):
            d = float(tr[s["trans"]].get("dur", 0.0))
        dfr = dc.nframes(d)
        if dfr > 0 and s["a"] <= f < s["a"] + dfr:
            prev = seglist[i - 1]
            outgoing = cutframe if prev["kind"] == "aroll" else source_frame(i - 1, prev, cutframe)
            p = (f - s["a"] + 1) / dfr
            base = vfx.transition(s["trans"], outgoing, base, p)
        else:
            # fecha o reader do segmento anterior quando a janela de transição acaba
            if i - 1 in readers and f >= s["a"] + max(dfr, 1):
                readers.pop(i - 1).close()
        # punch-in só no A-roll fora de transição
        if s["kind"] == "aroll" and dfr == 0:
            z = zoom_at(f, wins)
            if z:
                scale, fy = z
                base = vfx.zoom_frame(base, scale, fx=0.5, fy=fy)
        w.write(base)
    cut.close()
    for r in readers.values():
        r.close()
    w.close()
    return out, total_f


def pass_overlays(plan, W_, segs, st, base_mkv, total_f):
    man = dc.load_json(W_ / "graphics_manifest.json")["items"] if (W_ / "graphics_manifest.json").exists() else []
    # chroma overlays
    chroma = []
    cc = st["chroma"]
    for s in plan["shots"]:
        if s.get("kind") != "chroma":
            continue
        a = dc.nframes(dc.map_t(s["t"], segs))
        b = dc.nframes(dc.map_t(float(s["t"]) + float(s["dur"]), segs))
        chroma.append({"a": a, "b": b, "reader": vfx.Reader(dc.resolve(s["file"], plan["_dir"])),
                       "pos": s.get("pos", [500, 560]), "w": int(s.get("w", 540)),
                       "sim": float(s.get("similarity", cc["similarity"])), "blend": float(s.get("blend", cc["blend"]))})

    seg_overlays = Segmenter = None
    if any(m.get("behind") for m in man):
        seg_overlays = vfx.Segmenter(dc.MODELS / "selfie_segmenter.tflite")

    base = vfx.Reader(base_mkv)
    out = W_ / "director.mkv"
    w = vfx.Writer(out, crf=st["render"]["crf_intermediate"], preset=st["render"]["preset_intermediate"])
    dc.log(f"compose overlays: {len(man)} gráfico(s), {len(chroma)} chroma")
    import cv2
    for f in range(total_f):
        frame = base.read()
        if frame is None:
            frame = np.zeros((vfx.H, vfx.W, 3), np.uint8)
        frame = np.array(frame, copy=True)  # Reader devolve buffer somente-leitura
        # chroma primeiro (fica "no mundo", atrás dos gráficos)
        for ch in chroma:
            if not (ch["a"] <= f < ch["b"]):
                continue
            g = ch["reader"].read()
            if g is None:
                continue
            a = vfx.refine_alpha(vfx.key_green(g, ch["sim"], ch["blend"]), cc["erode_px"], cc["feather_px"])
            fg = vfx.despill(g)
            fg = vfx.color_match(fg, frame, a, cc["color_match"])
            fg = vfx.light_wrap(fg, frame, a, cc["light_wrap"])
            # escala e posiciona
            scale = ch["w"] / vfx.W
            nh = int(vfx.H * scale)
            fgs = cv2.resize(fg, (ch["w"], nh), interpolation=cv2.INTER_AREA)
            als = cv2.resize(a, (ch["w"], nh), interpolation=cv2.INTER_AREA)
            x, y = ch["pos"]
            x1, y1 = min(vfx.W, x + ch["w"]), min(vfx.H, y + nh)
            if x < vfx.W and y < vfx.H:
                roi = frame[y:y1, x:x1].astype(np.float32)
                al = als[: y1 - y, : x1 - x][..., None]
                frame[y:y1, x:x1] = (fgs[: y1 - y, : x1 - x].astype(np.float32) * al + roi * (1 - al)).astype(np.uint8)
        # gráficos
        person = None
        for m in man:
            k = f - m["start_frame"]
            if not (0 <= k < m["nframes"]):
                continue
            png = Path(m["dir"]) / f"frame-{k:02d}.png"
            if not png.exists():
                png = Path(m["dir"]) / f"frame-{k}.png"
            res = vfx.read_png_rgba(png)
            if res is None:
                continue
            crop, bbox = res
            if m.get("behind") and seg_overlays is not None:
                if person is None:
                    person = seg_overlays.mask(frame).copy()
                    person_src = frame.copy()
                vfx.over_rgba(frame, crop, bbox, m.get("opacity", 1.0))
                a3 = person[..., None]
                frame[:] = (person_src.astype(np.float32) * a3 + frame.astype(np.float32) * (1 - a3)).astype(np.uint8)
            else:
                vfx.over_rgba(frame, crop, bbox, m.get("opacity", 1.0))
        w.write(frame)
    base.close()
    for ch in chroma:
        ch["reader"].close()
    w.close()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    args = ap.parse_args()
    plan = dc.load_plan(args.plan)
    W_ = dc.workdir_for(plan)
    st = dc.load_style(plan.get("style", "director"))
    if not (W_ / "cut.mkv").exists():
        dc.die("cut.mkv não existe — rode o corte (02_apply_edl) antes")
    segs, _ = dc.timemap(W_)
    base_mkv, total_f = pass_base(plan, W_, segs, st)
    out = pass_overlays(plan, W_, segs, st, base_mkv, total_f)
    dc.save_json(W_ / "compose_log.json", {"frames": total_f, "shots": len(plan["shots"]),
                                           "graphics": len(plan["graphics"]), "out": str(out)})
    dc.log(f"OK: {out} ({total_f} quadros)")


if __name__ == "__main__":
    main()
