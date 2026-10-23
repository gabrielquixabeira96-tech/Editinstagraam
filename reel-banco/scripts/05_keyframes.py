#!/usr/bin/env python3
"""05 — Quadros-chave de um item de vídeo: 1 por cena (no meio) e no mínimo 1 a cada --every s (padrão 30).

Saídas:
  work/banco/<item_id>/keyframes/kNNN.jpg     quadro em resolução de trabalho (lado maior 1280 px)
  work/banco/<item_id>/keyframes.json         [{k, t, cena, arquivo, thumb}]
  index/items/<item_id>/thumbs/kNNN.jpg       miniatura 320 px de largura (versionada no git)
  index/items/<item_id>/contact_sheet.jpg     grade numerada (kNNN + tempo) — até --per-sheet quadros por
                                              folha; vídeos longos geram contact_sheet_02.jpg, _03 ...
Requer scenes.json (04_scenes.py).

Uso:
  python scripts/05_keyframes.py <item_id> [--every 30] [--cols 6] [--per-sheet 48] [--force]
  python scripts/05_keyframes.py <item_id> --sheets-only     # refaz só thumbs/contact sheet a partir dos keyframes
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402

THUMB_W = 320


def pick_times(scenes, dur, every):
    """1 quadro no meio de cada cena + quadros extras para que nenhum intervalo passe de `every` s."""
    pts = sorted((round((s["t0"] + s["t1"]) / 2, 2), s["n"]) for s in scenes)
    if not pts:
        return [(round(dur / 2, 2), 1)]

    def scene_at(t):
        return next((s["n"] for s in scenes if s["t0"] <= t < s["t1"]), scenes[-1]["n"])

    out = []
    prev = 0.0
    for t, n in pts + [(dur, None)]:
        gap = t - prev
        if gap > every:
            k = int(gap // every)
            step = gap / (k + 1)
            for j in range(1, k + 1):
                tt = round(prev + j * step, 2)
                out.append((tt, scene_at(tt)))
        if n is not None:
            out.append((t, n))
        prev = t
    return out


def grab(src, t, dest, maxside=1280):
    bc.run([bc.ffmpeg_bin(), "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
            "-vf", f"scale='if(gt(iw,ih),min({maxside},iw),-2)':'if(gt(iw,ih),-2,min({maxside},ih))'",
            "-q:v", "3", str(dest)])


def make_sheets(item_id, kfs, cols=6, per_sheet=48):
    from PIL import Image, ImageDraw, ImageFont
    idx = bc.index_item_dir(item_id)
    tdir = idx / "thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    for f in idx.glob("contact_sheet*.jpg"):
        f.unlink()
    thumbs = []
    for kf in kfs:
        src = bc.item_dir(item_id) / "keyframes" / kf["arquivo"]
        tp = tdir / kf["arquivo"]
        if src.exists():
            im = Image.open(src).convert("RGB")
            h = int(round(im.height * THUMB_W / im.width))
            im = im.resize((THUMB_W, h), Image.LANCZOS)
            im.save(tp, quality=82)
        elif tp.exists():
            im = Image.open(tp).convert("RGB")
        else:
            continue
        kf["thumb"] = f"thumbs/{kf['arquivo']}"
        thumbs.append((kf, im))
    if not thumbs:
        return []
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    cw = THUMB_W
    ch = max(im.height for _, im in thumbs)
    sheets = []
    for si in range(0, len(thumbs), per_sheet):
        chunk = thumbs[si:si + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        ncols = min(cols, len(chunk))
        pad, head = 6, 30
        sheet = Image.new("RGB", (ncols * (cw + pad) + pad, rows * (ch + head + pad) + pad), (16, 18, 22))
        dr = ImageDraw.Draw(sheet)
        for j, (kf, im) in enumerate(chunk):
            x = pad + (j % cols) * (cw + pad)
            y = pad + (j // cols) * (ch + head + pad)
            sheet.paste(im, (x, y + head))
            dr.text((x + 4, y + 4), f"{kf['k']}  {bc.fmt_t(kf['t'])}  c{kf['cena']}", fill=(57, 229, 140), font=font)
        name = "contact_sheet.jpg" if si == 0 else f"contact_sheet_{si // per_sheet + 1:02d}.jpg"
        sheet.save(idx / name, quality=80)
        sheets.append(name)
    return sheets


def main():
    ap = argparse.ArgumentParser(description="Quadros-chave, miniaturas 320 px e contact sheet de um item de vídeo.")
    ap.add_argument("item_id")
    ap.add_argument("--every", type=float, default=30.0, help="no mínimo 1 quadro a cada N s")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--per-sheet", type=int, default=48)
    ap.add_argument("--sheets-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    bc.set_log_item(args.item_id)
    item = bc.load_item(args.item_id)
    d = bc.item_dir(args.item_id)
    kjson = d / "keyframes.json"
    if args.sheets_only:
        if not kjson.exists():
            bc.die("keyframes.json ausente — rode sem --sheets-only")
        kfs = bc.load_json(kjson)
        sheets = make_sheets(args.item_id, kfs, args.cols, args.per_sheet)
        bc.save_json(kjson, kfs)
        bc.log(f"{args.item_id}: {len(sheets)} contact sheet(s) refeito(s)")
        return
    sc_path = d / "scenes.json"
    if not sc_path.exists():
        bc.die("scenes.json ausente — rode 04_scenes.py antes")
    src = bc.find_source(args.item_id)
    if not src:
        bc.die("mídia ausente")
    st = bc.State(args.item_id)
    signature = bc.sig([bc.file_sig(sc_path), args.every, args.cols, args.per_sheet, 2])
    sheet0 = bc.index_item_dir(args.item_id) / "contact_sheet.jpg"
    if not args.force and st.fresh("keyframes", signature, [kjson, sheet0]):
        bc.log(f"{args.item_id}: keyframes em cache")
        return
    scenes = bc.load_json(sc_path)["scenes"]
    dur = float(item.get("duracao") or scenes[-1]["t1"])
    times = pick_times(scenes, dur, args.every)
    kdir = d / "keyframes"
    kdir.mkdir(parents=True, exist_ok=True)
    for f in kdir.glob("k*.jpg"):
        f.unlink()
    kfs = []
    bc.log(f"{args.item_id}: extraindo {len(times)} quadros-chave ...")
    for i, (t, n) in enumerate(times, 1):
        name = f"k{i:03d}.jpg"
        grab(src, min(t, max(0.0, dur - 0.05)), kdir / name)
        if (kdir / name).exists():
            kfs.append({"k": f"k{i:03d}", "t": t, "cena": n, "arquivo": name})
    tdir = bc.index_item_dir(args.item_id) / "thumbs"
    if tdir.exists():
        for f in tdir.glob("k*.jpg"):
            f.unlink()
    sheets = make_sheets(args.item_id, kfs, args.cols, args.per_sheet)
    bc.save_json(kjson, kfs)
    st.done("keyframes", signature, [kjson, sheet0], n=len(kfs))
    item["n_keyframes"] = len(kfs)
    item["contact_sheets"] = sheets
    bc.save_item(item)
    bc.log(f"{args.item_id}: OK {len(kfs)} quadros, {len(sheets)} contact sheet(s) em {bc.index_item_dir(args.item_id)}")


if __name__ == "__main__":
    main()
