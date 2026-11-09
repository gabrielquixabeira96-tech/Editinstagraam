#!/usr/bin/env python3
"""00 — Pré-corte: bruto longo -> clipe só com os trechos escolhidos (+ transcrição remapeada, sem retranscrever).

Para brutos longos (ex.: 14 min de fala para um Reel de 60 s), rodar ingest + reel-polish no arquivo inteiro custaria
horas (polish ≈ 33 s de máquina por s de vídeo em 2 vCPU). Este script emenda só as faixas escolhidas pelo Claude
(com margem) num clipe intermediário; o resto do pipeline (ingest -> polish -> cut) trabalha sobre ele.

* Cada faixa é recodificada à parte com busca rápida (-ss antes do -i: o ffmpeg decodifica do keyframe anterior e
  descarta até o ponto — exato ao quadro na recodificação), HDR HLG/PQ -> BT.709 (mesma receita do 01_ingest),
  30 fps CFR, áudio 48 kHz estéreo; depois as faixas são emendadas (concat demuxer, mesmos parâmetros, cópia).
* Transcrição: NUNCA retranscreve. Recorta as palavras do transcript.json do bruto inteiro que caem em cada faixa
  e desloca os tempos para a timeline do clipe -> <saida>.transcript.json (formato reel-editor). Com --workdir,
  grava também <workdir>/transcript.json (o run_all pula a transcrição se ele existir).
* <saida>.precut.json: mapa {src_start, src_end, dst_start, dur} por faixa, para relatar cortes no tempo do bruto.

Uso:
  python scripts/00_precut.py BRUTO.MOV --ranges 358.5-361.7,425.8-446.6 --transcript full/transcript.json \
      --out work_in/reel.mp4 [--workdir work/reel]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
FPS = 30


def run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERRO ({r.returncode}): {' '.join(str(c) for c in cmd[:8])} ...\n{r.stderr[-2500:]}")
    return r


def is_hdr(src) -> bool:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=color_transfer,color_primaries", "-of", "json", src])
    st = (json.loads(r.stdout).get("streams") or [{}])[0]
    return st.get("color_transfer") in ("arib-std-b67", "smpte2084") or st.get("color_primaries") == "bt2020"


def parse_ranges(s):
    out = []
    for part in s.split(","):
        a, b = part.split("-")
        a, b = round(float(a) * FPS) / FPS, round(float(b) * FPS) / FPS
        if b <= a:
            sys.exit(f"faixa inválida: {part}")
        out.append((a, b))
    out.sort()
    for (a0, b0), (a1, b1) in zip(out, out[1:]):
        if a1 < b0:
            sys.exit(f"faixas sobrepostas: {a0}-{b0} e {a1}-{b1}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bruto")
    ap.add_argument("--ranges", required=True, help="a-b,c-d (segundos do bruto)")
    ap.add_argument("--transcript", required=True, help="transcript.json do bruto inteiro (reel-editor)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", help="também grava <workdir>/transcript.json")
    ap.add_argument("--crf", type=int, default=12)
    args = ap.parse_args()

    src = Path(args.bruto).resolve()
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    ranges = parse_ranges(args.ranges)
    hdr = is_hdr(src)
    vf = (TONEMAP + "," if hdr else "") + f"fps={FPS},setsar=1,format=yuv420p"

    tmp = Path(tempfile.mkdtemp(prefix="precut_", dir=str(out.parent)))
    parts, mapping, dst = [], [], 0.0
    for k, (a, b) in enumerate(ranges):
        p = tmp / f"p{k:02d}.mp4"
        nfr = int(round((b - a) * FPS))
        run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", src,
             "-t", f"{b - a:.3f}", "-map", "0:v:0", "-map", "0:a:0", "-vf", vf, "-frames:v", str(nfr),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", str(args.crf), "-pix_fmt", "yuv420p",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
             "-af", f"aresample=48000,apad,atrim=0:{b - a:.3f}", "-c:a", "pcm_s16le", "-ac", "2",
             "-metadata:s:v", "rotate=0", p.with_suffix(".mov")])
        parts.append(p.with_suffix(".mov"))
        mapping.append({"src_start": a, "src_end": b, "dst_start": round(dst, 3), "dur": round(b - a, 3)})
        dst += nfr / FPS
        print(f"  faixa {k + 1}/{len(ranges)}: {a:.2f}-{b:.2f}s -> {mapping[-1]['dst_start']:.2f}s", flush=True)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", out])
    for p in parts:
        p.unlink()
    lst.unlink()
    tmp.rmdir()

    tr = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
    words = []
    for m in mapping:
        for w in tr["words"]:
            if w["start"] >= m["src_start"] and w["end"] <= m["src_end"]:
                d = dict(w)
                d["start"] = round(w["start"] - m["src_start"] + m["dst_start"], 3)
                d["end"] = round(w["end"] - m["src_start"] + m["dst_start"], 3)
                d["src_start"] = w["start"]
                words.append(d)
    ntr = {k: v for k, v in tr.items() if k not in ("words", "segments")}
    ntr.update({"source": str(out), "duration": round(dst, 3), "words": words, "segments": [],
                "precut_of": str(src), "precut_map": mapping,
                "_nota": "remapeado por 00_precut.py a partir da transcrição do bruto inteiro (não retranscrito)"})
    tj = out.with_suffix(".transcript.json")
    tj.write_text(json.dumps(ntr, ensure_ascii=False, indent=1), encoding="utf-8")
    out.with_suffix(".precut.json").write_text(json.dumps({"bruto": str(src), "hdr_tonemapped": hdr,
                                                           "ranges": mapping, "duration": round(dst, 3)},
                                                          ensure_ascii=False, indent=1), encoding="utf-8")
    if args.workdir:
        wd = Path(args.workdir)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "transcript.json").write_text(json.dumps(ntr, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: {out} ({dst:.2f}s, {len(words)} palavras, HDR->709={hdr})")


if __name__ == "__main__":
    main()
