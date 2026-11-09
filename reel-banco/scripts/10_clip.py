#!/usr/bin/env python3
"""10 — Recorta um segmento do banco para usar num Reel.

Vídeo: work/banco/<item>/source.* [t0 - pad, t1 + pad] -> work/banco/clips/<segment_id>.mp4
       (# do id vira "_" no nome do arquivo), H.264 (yuv420p, CRF 18) + AAC 192 kbps, no máximo 1080p
       (lado menor ≤ 1080, preserva proporção e rotação), 30 fps se a fonte for VFR/>60.
       Padrão: recodifica com -ss antes do -i (busca rápida; o ffmpeg decodifica a partir do keyframe anterior e
       descarta até o ponto pedido — normalmente exato, ±1 quadro em alguns codecs/VFR). --precise usa busca
       HÍBRIDA: -ss (a−5 s) antes do -i (pula rápido para perto) e -ss 5 s depois do -i (decodifica só os 5 s
       finais quadro a quadro): exato e sem decodificar o arquivo desde o início.
       Se a mídia não está em work/, faz o fetch (02_fetch.py --item) antes — exige origem.drive_id ou
       origem.local existente (ver "reabastecer work/" no SKILL.md).
Documento: copia a figura/captura do segmento (ref.figura ou ref.secao_id; senão a página) para
       work/banco/clips/<segment_id>.png.

--broll --broll-index CAMINHO: acrescenta/atualiza a entrada no formato do banco de cenas da reel-director
(`assets/broll/index.json`): {"file", "source", "work", "what", "license_note", "tag", + extras
"segment_id", "dur", "tags", "t0", "t1", "kind"}. Se --broll-dir for dado (padrão: pasta do índice), o clipe é
COPIADO para lá e "file" fica relativo a ela (ex.: "assets/broll/<arquivo>" quando o índice está em assets/broll/).

Uso:
  python scripts/10_clip.py "<item_id>#s03" [--pad 0.3] [--precise] [--no-audio]
  python scripts/10_clip.py "<item_id>#s03" --broll --broll-index /home/claude/reel-director/assets/broll/index.json
  python scripts/10_clip.py "<item_id>#s03" --for-editor /home/claude/reel-director/work
Vídeo com transcrição: grava também <clip>.transcript.json (formato reel-editor, tempos relativos ao clipe) e,
com --for-editor DIR, DIR/<nome-do-clipe>/transcript.json — exatamente o work/<nome-do-video>/ que a
reel-editor/reel-director usam para um vídeo chamado <nome-do-clipe>.mp4.
Imprime CLIP=<caminho> no fim.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402


def find_segment(seg_id):
    item_id, _ = bc.split_seg_id(seg_id)
    segs = bc.load_segments(item_id, prefer_index=True) or []
    s = next((x for x in segs if x.get("id") == seg_id), None)
    if not s:
        bc.die(f"segmento {seg_id} não encontrado em segments.json de {item_id}")
    return item_id, s


TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv")  # mesma receita do 01_ingest da reel-director


def _is_hdr(src) -> bool:
    r = bc.run([bc.ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=color_transfer,color_primaries", "-of", "json", str(src)], check=False)
    try:
        st = json.loads(r.stdout or "{}").get("streams", [{}])[0]
    except ValueError:
        return False
    return st.get("color_transfer") in ("arib-std-b67", "smpte2084") or st.get("color_primaries") == "bt2020"


def _encode(src, a, b, out: Path, vf, precise, no_audio):
    cmd = [bc.ffmpeg_bin(), "-nostdin", "-v", "error", "-y"]
    if precise:
        pre = min(5.0, a)
        cmd += ["-ss", f"{a - pre:.3f}", "-i", str(src), "-ss", f"{pre:.3f}", "-t", f"{b - a:.3f}"]
    else:
        cmd += ["-ss", f"{a:.3f}", "-i", str(src), "-t", f"{b - a:.3f}"]
    cmd += ["-map", "0:v:0?", "-map", "0:a:0?", "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-movflags", "+faststart"]
    if any(x.startswith("zscale") for x in vf):
        cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    cmd += ["-an"] if no_audio else ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += [str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    bc.run(cmd)


def clip_video(item, s, out: Path, pad: float, precise: bool, no_audio: bool, short_side: int = 1080,
               stream: bool = False, drive_id: str | None = None, tonemap: bool = False, ranges=None):
    """ranges: lista [(a, b)] em segundos ABSOLUTOS do item (podem cair fora do segmento, no mesmo item):
    cada trecho é recodificado e os trechos são emendados (concat sem recodificar) num clipe só, na ordem dada.
    stream: sem mídia em work/, lê direto do Drive por faixas (drive_stream.py) — não baixa o arquivo inteiro."""
    src = bc.find_source(item["id"])
    ctx = None
    if not src and stream:
        import drive_stream as ds_mod  # noqa: E402
        fid = drive_id or (item.get("origem") or {}).get("drive_id")
        if not fid:
            bc.die("--stream exige origem.drive_id no item ou --drive-id")
        ctx = ds_mod.DriveStream(fid)
        src = ctx.__enter__()
        bc.log(f"{item['id']}: lendo do Drive por faixas (sem download completo) — id {fid}")
    elif not src:
        bc.log(f"{item['id']}: mídia ausente em work/ — buscando ...")
        bc.run_script("02_fetch.py", "--item", item["id"])
        src = bc.find_source(item["id"])
        if not src:
            bc.die("não foi possível obter a mídia")
    try:
        dur = float(item.get("duracao") or bc.probe(src).get("duration") or 0)
        w, h = item.get("largura") or 1920, item.get("altura") or 1080
        vf = []
        if tonemap and _is_hdr(src):
            vf.append(TONEMAP)
            bc.log("HDR (HLG/PQ) -> BT.709 por tone-map")
        if min(w, h) > short_side:
            vf.append(f"scale=-2:{short_side}" if w >= h else f"scale={short_side}:-2")
        fps = item.get("fps") or 30
        if fps > 60 or fps <= 0:
            vf.append("fps=30")
        vf.append("format=yuv420p")
        if not ranges:
            a = max(0.0, float(s["t0"]) - pad)
            b = min(dur, float(s["t1"]) + pad) if dur else float(s["t1"]) + pad
            _encode(src, a, b, out, vf, precise, no_audio)
            spans = [(a, b, 0.0)]
        else:
            tmp = out.parent / f".{out.stem}_parts"
            tmp.mkdir(parents=True, exist_ok=True)
            parts, spans, off = [], [], 0.0
            for k, (ra, rb) in enumerate(ranges):
                ra, rb = max(0.0, ra), (min(dur, rb) if dur else rb)
                pp = tmp / f"p{k:02d}.mp4"
                bc.log(f"  trecho {k + 1}/{len(ranges)}: {bc.fmt_t(ra)}–{bc.fmt_t(rb)}")
                _encode(src, ra, rb, pp, vf, precise, no_audio)
                pdur = float(bc.probe(pp).get("duration") or (rb - ra))
                parts.append(pp)
                spans.append((ra, ra + pdur, off))
                off += pdur
            lst = tmp / "list.txt"
            lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
            bc.run([bc.ffmpeg_bin(), "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", "-movflags", "+faststart", str(out)])
            shutil.rmtree(tmp, ignore_errors=True)
            a, b = spans[0][0], spans[-1][1]
    finally:
        if ctx is not None:
            bc.log(f"lidos do Drive: {ctx.bytes_read / 1e6:.1f} MB")
            ctx.__exit__(None, None, None)
    info = bc.probe(out)
    if not info.get("ok") or not info.get("has_video"):
        bc.die(f"clipe inválido: {out}")
    info["spans"] = spans
    return info, a, b


def clip_transcript(item, a, b, clip: Path, spans=None):
    """transcript.json do clipe (formato reel-editor), com tempos deslocados para o clipe: pronto para a reel-editor
    usar o clipe como fonte sem retranscrever. Grava <clip>.transcript.json. spans = [(a, b, deslocamento_no_clipe)]
    (vários trechos emendados); palavras que atravessam a borda de um trecho ficam de fora."""
    tr = bc.load_transcript(item["id"])
    if not tr:
        return None
    spans = spans or [(a, b, 0.0)]
    words, segs = [], []
    for sa, sb, off in spans:
        sh = lambda t, sa=sa, sb=sb, off=off: round(off + min(max(0.0, t - sa), sb - sa), 3)  # noqa: E731
        for w in tr.get("words", []):
            if w["start"] >= sa and w["end"] <= sb + 0.05:
                nw = dict(w)
                for k in ("start", "end", "raw_start", "raw_end"):
                    if k in nw:
                        nw[k] = sh(nw[k])
                words.append(nw)
        for sg in tr.get("segments", []):
            if sg["end"] > sa and sg["start"] < sb:
                segs.append({**sg, "start": sh(max(sg["start"], sa)), "end": sh(min(sg["end"], sb))})
    total = spans[-1][2] + (spans[-1][1] - spans[-1][0])
    out = clip.with_suffix(".transcript.json")
    extra = {"clip_ranges": [{"t0": round(sa, 3), "t1": round(sb, 3), "offset": round(off, 3)} for sa, sb, off in spans]}
    bc.save_json(out, {**{k: v for k, v in tr.items() if k not in ("words", "segments")},
                       "source": str(clip), "duration": round(total, 3), "clip_of": item["id"],
                       "clip_offset": round(a, 3), **(extra if len(spans) > 1 else {}),
                       "generated_by": "reel-banco/10_clip.py (recorte, sem retranscrever)",
                       "words": words, "segments": segs})
    return out


def clip_doc(item, s, out_base: Path):
    ref = s.get("ref") or {}
    d = bc.item_dir(item["id"])
    fj = d / "figuras.json"
    if not fj.exists():
        fj = bc.index_item_dir(item["id"]) / "figuras.json"  # sessão nova: work/ vazio, índice versionado
    figs = bc.load_json(fj) if fj.exists() else []
    cand = None
    key = ref.get("figura") or ref.get("secao_id")
    if key:
        f = next((x for x in figs if x["id"] == key), None)
        if f:
            cand = d / f["arquivo"]
    if cand is None and "pagina" in ref:
        cand = d / "figuras" / f"p{ref['pagina']}.jpg"
    if cand is None and "secao" in ref:
        cand = d / "secoes" / f"s{int(ref['secao']):02d}.png"
    if cand is None or not cand.exists():
        o = item.get("origem", {})
        onde = (f"com a ferramenta Artifact (action read, artefato {o['artifact_id']})" if o.get("artifact_id") else
                f"pelo conector Drive (id {o.get('drive_id') or '?'}, '{o.get('nome_original')}')")
        bc.die(f"{s['id']}: figura/captura ausente em {d} (work/ não é versionado). Reabasteça: baixe o documento "
               f"{onde} e rode "
               f"07_doc_ingest.py --local <arquivo> --licenca {item.get('licenca')} --item-id {item['id']}"
               + (f" --fonte \"{item['fonte']}\"" if item.get("fonte") else "") + " — depois repita este comando.")
    out = out_base.with_suffix(cand.suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cand, out)
    fig = next((x for x in figs if x.get("arquivo") and (d / x["arquivo"]) == cand), None)
    return out, fig


def doc_license(item, s, fig=None):
    """licença efetiva do que sai de um documento: a da FIGURA (imagem de artigo dentro de um atlas próprio é de
    terceiros), senão a do segmento, senão a do item. Captura de seção com figuras de terceiros: creditar todas."""
    info = {"licenca": item.get("licenca", "desconhecida"), "fonte": item.get("fonte"), "detalhe": None,
            "creditos": [], "uso_comercial": None}
    if s.get("licenca"):
        info.update(licenca=s["licenca"], fonte=s.get("fonte") or info["fonte"])
    if fig:
        if fig.get("tipo") == "imagem" or (fig.get("licenca") and fig.get("licenca") != info["licenca"]
                                            and fig.get("tipo") != "secao"):
            info.update(licenca=fig.get("licenca", info["licenca"]), fonte=fig.get("fonte") or info["fonte"],
                        detalhe=fig.get("licenca_detalhe"))
        if fig.get("uso_comercial") is False:
            info["uso_comercial"] = False
        info["creditos"] = list(fig.get("contem_terceiros") or [])
    return info


def license_note(info):
    lic = info["licenca"]
    det = f" {info['detalhe']}" if info.get("detalhe") else ""
    note = {"propria": "material próprio do Gabriel",
            "terceiros": f"terceiros{det} — creditar a fonte na tela ({info.get('fonte') or 'fonte não registrada'})",
            "desconhecida": "licença desconhecida — confirmar com o Gabriel antes de publicar"}.get(lic, lic)
    if info.get("creditos"):
        note += " — CONTÉM figuras de terceiros: creditar na tela " + " | ".join(info["creditos"])
    if info.get("uso_comercial") is False:
        note += " — licença NC: não usar em conteúdo patrocinado/comercial"
    return note


def add_broll_entry(index_path: Path, clip: Path, item, s, a=None, b=None, broll_dir=None, lic_info=None):
    index_path = Path(index_path)
    data = bc.load_json(index_path) if index_path.exists() else []
    lst = data.get("items", data.get("clips", [])) if isinstance(data, dict) else data
    bdir = Path(broll_dir) if broll_dir else index_path.parent
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / clip.name
    if clip.resolve() != dest.resolve():
        shutil.copy2(clip, dest)
    # caminho como a reel-director o referencia: relativo à raiz da skill quando o índice está em assets/broll/
    if index_path.parent.name == "broll" and index_path.parent.parent.name == "assets":
        rel = f"assets/broll/{dest.name}" if bdir.resolve() == index_path.parent.resolve() else str(dest)
    else:
        rel = str(dest)
    o = item.get("origem", {})
    info = lic_info or doc_license(item, s)
    src_desc = info.get("fonte") or item.get("fonte") or (f"Google Drive id {o['drive_id']}" if o.get("drive_id")
                                                          else o.get("nome_original"))
    lic = info["licenca"]
    lic_note = license_note(info)
    entry = {"file": rel, "source": src_desc, "work": item.get("titulo_doc") or item.get("nome"),
             "what": s.get("tema"), "license_note": lic_note, "tag": (s.get("tags") or ["banco"])[0],
             "tags": s.get("tags", []), "segment_id": s["id"], "kind": "video" if a is not None else "image",
             "dur": round(b - a, 3) if a is not None else None,
             "t0": round(a, 3) if a is not None else None, "t1": round(b, 3) if b is not None else None, "licenca": lic,
             "added_by": "reel-banco/10_clip.py", "added_at": bc.now_iso()}
    lst = [e for e in lst if e.get("segment_id") != s["id"] and e.get("file") != rel]
    lst.append(entry)
    if isinstance(data, dict):
        data["items" if "items" in data or "clips" not in data else "clips"] = lst
    else:
        data = lst
    bc.save_json(index_path, data)
    return entry


def main():
    ap = argparse.ArgumentParser(description="Recorta um segmento do banco (vídeo -> mp4; doc -> imagem).")
    ap.add_argument("segment_id", help='ex.: "20261020_aula-vias-aereas#s03" (use aspas por causa do #)')
    ap.add_argument("--pad", type=float, default=0.3, help="folga antes/depois (s)")
    ap.add_argument("--precise", action="store_true",
                    help="corte exato por busca híbrida (-ss a-5 antes do -i, -ss 5 depois)")
    ap.add_argument("--for-editor", metavar="DIR",
                    help="pasta work/ da reel-editor/reel-director (ex.: /home/claude/reel-director/work): grava "
                         "DIR/<nome-do-clipe>/transcript.json para elas não retranscreverem o clipe")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--short-side", type=int, default=1080,
                    help="lado menor máximo do clipe (padrão 1080; use 2160 para manter o 4K quando o clipe vai ser "
                         "reenquadrado em 9:16 pela reel-polish — evita ampliar o recorte)")
    ap.add_argument("--out", help="caminho de saída (padrão: work/banco/clips/<id>.mp4)")
    ap.add_argument("--stream", action="store_true",
                    help="mídia ausente em work/: lê direto do Drive por faixas HTTP (drive_stream.py), sem baixar o "
                         "arquivo inteiro — para brutos enormes (ex.: 4K de 45 min, 16 GB) quando só um trecho importa")
    ap.add_argument("--drive-id", help="com --stream: outro id do Drive com o MESMO arquivo (ex.: cópia que o Gabriel "
                                       "compartilhou, quando o link original estourou a cota)")
    ap.add_argument("--tonemap", action="store_true",
                    help="HDR HLG/PQ -> BT.709 (mesma receita do 01_ingest da reel-director); use quando o clipe vai "
                         "direto para a reel-polish (que não faz tone-map)")
    ap.add_argument("--ranges", help='trechos ABSOLUTOS do item a emendar num clipe só, na ordem: "1596.3-1604.4,'
                                     '1550.1-1562.9" (podem sair do segmento, mas são do mesmo item); a transcrição '
                                     'do clipe segue os trechos')
    ap.add_argument("--broll", action="store_true", help="registra no índice de B-roll da reel-director")
    ap.add_argument("--broll-index", help="caminho do assets/broll/index.json da reel-director")
    ap.add_argument("--broll-dir", help="pasta onde copiar o clipe (padrão: pasta do --broll-index)")
    args = ap.parse_args()

    item_id, s = find_segment(args.segment_id)
    bc.set_log_item(item_id)
    item = bc.load_item(item_id)
    safe = args.segment_id.replace("#", "_")
    out = Path(args.out) if args.out else bc.clips_dir() / f"{safe}.mp4"
    a = b = None
    lic_info = None
    if item.get("tipo") == "doc":
        out, fig = clip_doc(item, s, out)
        lic_info = doc_license(item, s, fig)
        bc.log(f"{args.segment_id}: imagem -> {out}")
        if lic_info["licenca"] != "propria" or lic_info["creditos"]:
            bc.warn(f"licença: {license_note(lic_info)} — avisar o usuário")
    else:
        if s.get("t0") is None:
            bc.die("segmento de vídeo sem t0/t1")
        rng = None
        if args.ranges:
            rng = [tuple(float(x) for x in r.split("-")) for r in args.ranges.split(",") if r.strip()]
            if any(len(r) != 2 or r[1] <= r[0] for r in rng):
                bc.die('--ranges inválido: use "a-b,c-d" com b > a (segundos)')
        info, a, b = clip_video(item, s, out, args.pad, args.precise, args.no_audio, args.short_side,
                                stream=args.stream, drive_id=args.drive_id, tonemap=args.tonemap, ranges=rng)
        bc.log(f"{args.segment_id}: {bc.fmt_t(a)}–{bc.fmt_t(b)} -> {out} ({info.get('width')}x{info.get('height')}, "
               f"{info.get('duration'):.2f} s)")
        tp = clip_transcript(item, a, b, out, info.get("spans"))
        if tp:
            bc.log(f"transcrição do clipe (formato reel-editor) -> {tp}")
            if args.for_editor:
                ed = Path(args.for_editor).expanduser() / out.stem
                ed.mkdir(parents=True, exist_ok=True)
                tr = bc.load_json(tp)
                tr["source"] = str(out.resolve())
                bc.save_json(ed / "transcript.json", tr, compact=True)
                bc.log(f"transcript para a reel-editor/reel-director -> {ed / 'transcript.json'}")
                print(f"EDITOR_WORKDIR={ed}")
        elif args.for_editor:
            bc.warn("item sem transcrição — nada gravado em --for-editor")
    if args.broll:
        if not args.broll_index:
            bc.die("--broll exige --broll-index <caminho do assets/broll/index.json>")
        e = add_broll_entry(Path(args.broll_index), out, item, s, a, b, args.broll_dir, lic_info)
        bc.log(f"B-roll registrado em {args.broll_index}: {e['file']}")
    print(f"CLIP={out}")


if __name__ == "__main__":
    main()
