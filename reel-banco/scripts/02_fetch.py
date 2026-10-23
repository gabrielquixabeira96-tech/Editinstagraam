#!/usr/bin/env python3
"""02 — Traz a mídia de um item para work/banco/<item_id>/source.<ext> e cria item.json.

Dois modos:
  --file-id ID --name NOME [--size BYTES] [--modified ISO]  vídeo do Drive via scripts/drive_fetch.py
                                                            (o arquivo/pasta precisa estar compartilhado
                                                            "qualquer pessoa com o link – leitor")
  --local CAMINHO [--name NOME] [--drive-id ID]             arquivo já presente no disco (vídeo ou doc
                                                            baixado pelo conector); é COPIADO para work/

item.json: {id, nome, tipo (video|doc), origem {drive_id, nome_original, mimeType, modifiedTime, local},
            tamanho, sha256, duracao, largura, altura, fps, tem_audio, licenca, status, criado_em}
Idempotente: se source.* já existe com o mesmo tamanho (e sha256, quando conhecido), não baixa de novo.

Uso:
  python scripts/02_fetch.py --file-id 1AbC... --name "aula vias aereas.mp4" --size 734003200
  python scripts/02_fetch.py --local ~/Downloads/live.mp4
  python scripts/02_fetch.py --item 20261020_aula-vias-aereas --refetch   # rebaixa a mídia de um item indexado
Imprime o item_id na última linha ("ITEM_ID=<id>").
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402


def fetch_drive(file_id, dest: Path, expect_size=None):
    cmd = [bc.py(), str(bc.SCRIPTS / "drive_fetch.py"), file_id, str(dest)]
    if expect_size:
        cmd += ["--expect-size", str(int(expect_size))]
    import subprocess
    r = subprocess.run(cmd)
    if r.returncode == 3:
        bc.die("o Drive pediu login: o arquivo/pasta 01_entrada NÃO está compartilhado por link "
               "('Qualquer pessoa com o link: Leitor'). Peça ao usuário para ajustar o compartilhamento.", 3)
    if r.returncode == 4:
        bc.die("o Drive devolveu HTML inesperado (cota de download excedida ou aviso novo). Tente mais tarde "
               "ou peça o arquivo por outro meio.", 4)
    if r.returncode != 0:
        bc.die(f"drive_fetch.py falhou (código {r.returncode})", r.returncode)


def build_item(item_id, src: Path, nome, origem, prev=None):
    info = bc.probe(src)
    tipo = bc.kind_of(src.name)
    if tipo == "ignorado":
        tipo = "video" if info.get("has_video") or info.get("has_audio") else "doc"
    it = dict(prev or {})
    it.update({
        "id": item_id, "nome": nome, "tipo": tipo, "origem": {**(prev or {}).get("origem", {}), **origem},
        "arquivo": src.name, "tamanho": src.stat().st_size, "sha256": bc.sha256_file(src),
    })
    if tipo == "video":
        it.update({"duracao": info.get("duration"), "largura": info.get("width"), "altura": info.get("height"),
                   "fps": info.get("fps"), "rotacao": info.get("rotation", 0), "tem_audio": info.get("has_audio"),
                   "vcodec": info.get("vcodec"), "acodec": info.get("acodec")})
        it.setdefault("licenca", "propria")
    it.setdefault("status", "baixado")
    it.setdefault("criado_em", bc.now_iso())
    return it


def main():
    ap = argparse.ArgumentParser(description="Traz a mídia de um item para work/banco/<item_id>/ e cria item.json.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file-id", help="id do arquivo no Google Drive")
    g.add_argument("--local", help="caminho de um arquivo local")
    g.add_argument("--item", help="item_id já indexado: rebaixa a mídia (usa origem.drive_id)")
    ap.add_argument("--name", help="nome original do arquivo (obrigatório com --file-id)")
    ap.add_argument("--size", type=int, help="tamanho em bytes vindo do manifesto (confere o download)")
    ap.add_argument("--modified", help="modifiedTime do Drive (ISO) — define a data do item_id")
    ap.add_argument("--drive-id", help="com --local: id do arquivo no Drive (para rastrear/mover depois)")
    ap.add_argument("--mime", help="mimeType do Drive (informativo)")
    ap.add_argument("--item-id", help="força o item_id (normalmente derivado de data+nome)")
    ap.add_argument("--licenca", choices=bc.LICENCAS, help="licença do material (padrão vídeo: propria)")
    ap.add_argument("--refetch", action="store_true", help="baixa de novo mesmo se source.* existir")
    args = ap.parse_args()

    prev = None
    if args.item:
        prev = bc.load_item(args.item)
        o = prev.get("origem", {})
        if not o.get("drive_id"):
            local = o.get("local")
            if local and Path(local).exists():
                args.local = local
            else:
                bc.die(f"{args.item}: mídia ausente e o item não tem origem.drive_id (foi ingerido com --local sem "
                       f"--drive-id) nem o arquivo local original ({local or '?'}). Reabasteça work/: obtenha o arquivo "
                       f"'{o.get('nome_original')}' (sha256 {str(prev.get('sha256'))[:12]}…) e rode "
                       f"02_fetch.py --local <arquivo> --item-id {args.item} [--drive-id <id>]")
        else:
            args.file_id = o["drive_id"]
        args.name = args.name or o.get("nome_original") or prev.get("nome")
        args.size = args.size or prev.get("tamanho")
        item_id = args.item
    else:
        if args.file_id and not args.name:
            bc.die("--name é obrigatório com --file-id")
        name = args.name or Path(args.local).name
        when = args.modified
        if when is None and args.local:
            when = Path(args.local).stat().st_mtime if Path(args.local).exists() else None
        item_id = args.item_id or bc.make_item_id(name, when)
        prev = bc.load_item(item_id, required=False)
    bc.set_log_item(item_id)
    d = bc.item_dir(item_id)
    d.mkdir(parents=True, exist_ok=True)
    name = args.name or Path(args.local).name
    ext = Path(name).suffix.lower() or ".bin"
    dest = d / f"source{ext}"

    existing = bc.find_source(item_id)
    if existing and not args.refetch:
        same = True
        if args.size and existing.stat().st_size != args.size:
            same = False
        if prev and prev.get("sha256") and prev.get("tamanho") == existing.stat().st_size:
            same = same and True
        if same:
            bc.log(f"{item_id}: mídia já presente ({existing.name}, {existing.stat().st_size / 1e6:.1f} MB) — não baixo de novo")
            if not prev or prev.get("tamanho") != existing.stat().st_size:
                origem = {"drive_id": args.file_id or args.drive_id, "nome_original": name, "mimeType": args.mime,
                          "modifiedTime": args.modified, "local": str(Path(args.local).resolve()) if args.local else None}
                origem = {k: v for k, v in origem.items() if v}
                it = build_item(item_id, existing, Path(name).stem, origem, prev)
                if args.licenca:
                    it["licenca"] = args.licenca
                bc.save_item(it)
            print(f"ITEM_ID={item_id}")
            return

    if args.file_id:
        bc.log(f"{item_id}: baixando do Drive {args.file_id} -> {dest}")
        fetch_drive(args.file_id, dest, args.size)
        origem = {"drive_id": args.file_id, "nome_original": name, "mimeType": args.mime,
                  "modifiedTime": args.modified}
    else:
        srcp = Path(os.path.expanduser(args.local)).resolve()
        if not srcp.exists():
            bc.die(f"arquivo local não encontrado: {srcp}")
        if srcp != dest.resolve():
            bc.log(f"{item_id}: copiando {srcp} -> {dest}")
            shutil.copy2(srcp, dest)
        origem = {"local": str(srcp), "nome_original": name, "drive_id": args.drive_id, "mimeType": args.mime,
                  "modifiedTime": args.modified}
        if not args.drive_id and not (prev or {}).get("origem", {}).get("drive_id"):
            bc.warn("ingerido com --local sem --drive-id: em sessão nova a mídia não poderá ser rebaixada sozinha "
                    "(work/ não é versionado). Passe --drive-id se o arquivo estiver no Drive.")
    origem = {k: v for k, v in origem.items() if v}
    it = build_item(item_id, dest, Path(name).stem, origem, prev)
    if args.licenca:
        it["licenca"] = args.licenca
    bc.save_item(it)
    dur = it.get("duracao")
    bc.log(f"{item_id}: OK tipo={it['tipo']} tamanho={it['tamanho'] / 1e6:.1f} MB"
           + (f" duração={bc.fmt_t(dur)}" if dur else ""))
    print(f"ITEM_ID={item_id}")


if __name__ == "__main__":
    main()
