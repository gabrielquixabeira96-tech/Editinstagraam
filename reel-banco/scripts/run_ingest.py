#!/usr/bin/env python3
"""run_ingest — encadeia a ingestão de um item e PARA no ponto em que o Claude precisa escrever segments.json.

Vídeo:   02_fetch -> 03_transcribe -> 04_scenes -> 05_keyframes -> 06_segment_input   (para: LER segment_input.md)
Doc:     07_doc_ingest                                                           (para: LER doc_input.md)
Depois que o Claude escreve work/banco/<item_id>/segments.json:
         run_ingest.py --finish <item_id>
           = 06b_validate_segments -> copia transcript.json/segments.json para index/items/<id>/ -> confere
             thumbs/contact sheet (refaz se faltarem) -> 08_index -> item.status = "indexado"

Cada etapa tem cache próprio; rodar de novo não refaz nada que já existe (e NUNCA retranscreve).

Uso:
  python scripts/run_ingest.py --file-id ID --name NOME [--size BYTES] [--modified ISO]
  python scripts/run_ingest.py --local CAMINHO [--licenca terceiros --fonte "..."] [--drive-id ID]
  python scripts/run_ingest.py --finish <item_id>
Opções repassadas: --model (whisper), --import-transcript (reaproveita transcript.json existente),
--threshold (scdet), --every (keyframes), --block (segment_input).
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402


def capture_item_id(args_list):
    import subprocess
    cmd = [bc.py(), str(bc.SCRIPTS / "02_fetch.py"), *args_list]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    iid = None
    for line in p.stdout:
        print(line, end="", flush=True)
        if line.startswith("ITEM_ID="):
            iid = line.strip().split("=", 1)[1]
    p.wait()
    if p.returncode != 0 or not iid:
        bc.die(f"02_fetch.py falhou (código {p.returncode})", p.returncode or 1)
    return iid


def finish(item_id):
    bc.set_log_item(item_id)
    item = bc.load_item(item_id)
    bc.run_script("06b_validate_segments.py", item_id)
    d, idx = bc.item_dir(item_id), bc.index_item_dir(item_id)
    idx.mkdir(parents=True, exist_ok=True)
    shutil.copy2(d / "segments.json", idx / "segments.json")
    if item.get("tipo") == "video":
        if (d / "transcript.json").exists():
            shutil.copy2(d / "transcript.json", idx / "transcript.json")
        if not (idx / "contact_sheet.jpg").exists() or not list((idx / "thumbs").glob("*.jpg")):
            if (d / "keyframes.json").exists():
                bc.run_script("05_keyframes.py", item_id, "--sheets-only")
            else:
                bc.warn("sem keyframes.json — contact sheet não gerado")
        if (d / "scenes.json").exists():
            shutil.copy2(d / "scenes.json", idx / "scenes.json")
    else:
        if (d / "doc_text.json").exists():
            shutil.copy2(d / "doc_text.json", idx / "doc_text.json")
    item = bc.load_item(item_id)
    item["status"] = "indexado"
    item["indexado_em"] = bc.now_iso()
    bc.save_item(item)
    bc.run_script("08_index.py")
    o = item.get("origem", {})
    bc.log(f"{item_id}: INDEXADO.")
    if o.get("drive_id"):
        print(f"MOVER_NO_DRIVE={o['drive_id']}  (01_entrada -> 02_processado)")
    print(f"COMMIT: git -C ~/reel-polish-src add reel-banco/index && "
          f"git -C ~/reel-polish-src commit -m 'banco: {item_id}' && git -C ~/reel-polish-src push")


def main():
    ap = argparse.ArgumentParser(description="Ingestão de um item do banco (para antes de segments.json).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file-id")
    g.add_argument("--local")
    g.add_argument("--finish", metavar="ITEM_ID")
    ap.add_argument("--name")
    ap.add_argument("--size", type=int)
    ap.add_argument("--modified")
    ap.add_argument("--drive-id")
    ap.add_argument("--item-id", help="força o item_id (reabastecimento de work/ de um item já indexado)")
    ap.add_argument("--licenca", choices=bc.LICENCAS)
    ap.add_argument("--fonte")
    ap.add_argument("--model")
    ap.add_argument("--import-transcript")
    ap.add_argument("--threshold")
    ap.add_argument("--every")
    ap.add_argument("--block")
    ap.add_argument("--offline", action="store_true", help="doc HTML: repassado ao 07_doc_ingest")
    ap.add_argument("--tema", choices=["light", "dark", "auto"], help="doc HTML: repassado ao 07_doc_ingest")
    ap.add_argument("--blocos", help="doc HTML: repassado ao 07_doc_ingest")
    ap.add_argument("--clicar", help="doc HTML: repassado ao 07_doc_ingest")
    args = ap.parse_args()

    if args.finish:
        finish(args.finish)
        return

    kind = "video"
    if args.local:
        kind = bc.kind_of(Path(args.local).name)
        if kind == "ignorado":
            bc.die(f"tipo não suportado: {args.local}")
    if kind == "doc":
        if not args.licenca:
            bc.die("documento exige --licenca propria|terceiros|desconhecida")
        cmd = ["--local", args.local, "--licenca", args.licenca]
        for k in ("fonte", "name", "drive_id", "modified", "item_id", "tema", "blocos", "clicar"):
            v = getattr(args, k)
            if v:
                cmd += [f"--{k.replace('_', '-')}", v]
        if args.offline:
            cmd.append("--offline")
        bc.run_script("07_doc_ingest.py", *cmd)
        print("PROXIMO: ler doc_input.md, escrever segments.json e rodar run_ingest.py --finish <item_id>")
        return

    fa = []
    if args.file_id:
        if not args.name:
            bc.die("--name é obrigatório com --file-id")
        fa = ["--file-id", args.file_id, "--name", args.name]
    else:
        fa = ["--local", args.local] + (["--name", args.name] if args.name else []) + \
             (["--item-id", args.item_id] if args.item_id else [])
        if args.drive_id:
            fa += ["--drive-id", args.drive_id]
    if args.size:
        fa += ["--size", str(args.size)]
    if args.modified:
        fa += ["--modified", args.modified]
    if args.licenca:
        fa += ["--licenca", args.licenca]
    iid = capture_item_id(fa)
    bc.set_log_item(iid)
    t = [iid] + (["--model", args.model] if args.model else []) + \
        (["--import-transcript", args.import_transcript] if args.import_transcript else [])
    item = bc.load_item(iid)
    if item.get("tem_audio", True):
        bc.run_script("03_transcribe.py", *t)
    else:
        bc.warn("sem trilha de áudio — transcrição pulada (material de B-roll)")
    bc.run_script("04_scenes.py", iid, *(["--threshold", args.threshold] if args.threshold else []))
    bc.run_script("05_keyframes.py", iid, *(["--every", args.every] if args.every else []))
    bc.run_script("06_segment_input.py", iid, *(["--block", args.block] if args.block else []))
    print(f"PROXIMO: ler {bc.item_dir(iid) / 'segment_input.md'}, escrever segments.json e rodar "
          f"run_ingest.py --finish {iid}")


if __name__ == "__main__":
    main()
