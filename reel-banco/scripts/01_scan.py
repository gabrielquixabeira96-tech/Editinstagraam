#!/usr/bin/env python3
"""01 — Compara a listagem da pasta 01_entrada (manifesto JSON montado pelo Claude a partir do conector
Google Drive) com o índice e devolve os arquivos AINDA NÃO ingeridos.

Manifesto aceito (lista ou {"files": [...]}), um objeto por arquivo, com os campos do conector:
  {"id": "1AbC...", "name": "aula_vias_aereas.mp4", "mimeType": "video/mp4",
   "size": "734003200", "modifiedTime": "2026-10-20T14:03:11Z"}

Um arquivo é considerado JÁ INGERIDO quando algum index/items/*/item.json tem o mesmo
origem.drive_id (ou o lista em fontes_adicionais[].drive_id — cópia .md, duplicata, wrapper de artefato),
ou o mesmo item_id (data+slug) e mesmo tamanho. Pastas e tipos não suportados
são listados como ignorados.

Uso:
  python scripts/01_scan.py --manifest manifest.json [--out pendentes.json] [--json]
Saída: tabela legível + (com --out) JSON {"novos": [...], "ja_ingeridos": [...], "ignorados": [...]};
cada novo traz item_id, tipo (video|doc) e o comando run_ingest sugerido.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402


def known_items():
    by_drive, by_id = {}, {}
    root = bc.index_root() / "items"
    for p in sorted(root.glob("*/item.json")):
        try:
            it = bc.load_json(p)
        except Exception:
            continue
        o = it.get("origem", {})
        if o.get("drive_id"):
            by_drive[o["drive_id"]] = it
        # outras cópias/versões do mesmo material no Drive (ex.: .md do artefato, duplicata): já cobertas pelo item
        for extra in it.get("fontes_adicionais") or []:
            if isinstance(extra, dict) and extra.get("drive_id"):
                by_drive.setdefault(extra["drive_id"], it)
        by_id[it.get("id")] = it
    return by_drive, by_id


def main():
    ap = argparse.ArgumentParser(description="Lista arquivos da 01_entrada ainda não ingeridos no banco.")
    ap.add_argument("--manifest", required=True, help="JSON com a listagem da pasta (id, name, mimeType, size, modifiedTime)")
    ap.add_argument("--out", help="grava o resultado em JSON neste caminho")
    ap.add_argument("--json", action="store_true", help="imprime o JSON em vez da tabela")
    args = ap.parse_args()

    man = bc.load_json(args.manifest)
    files = man.get("files", man) if isinstance(man, dict) else man
    by_drive, by_id = known_items()
    novos, ja, ign = [], [], []
    for f in files:
        name = f.get("name") or f.get("title") or ""
        mime = f.get("mimeType", "")
        if mime == "application/vnd.google-apps.folder":
            ign.append({**f, "motivo": "pasta"})
            continue
        if f.get("id") in by_drive:  # já ingerido (inclusive fontes adicionais de um item, de qualquer tipo)
            hit = by_drive[f["id"]]
            ja.append({"id": f.get("id"), "name": name, "item_id": hit["id"], "status": hit.get("status")})
            continue
        tipo = bc.kind_of(name, mime)
        if mime.startswith("application/vnd.google-apps"):
            tipo = "doc"  # Google Docs: o Claude baixa/exporta via conector e ingere com --local
        if tipo == "ignorado":
            ign.append({**f, "motivo": f"tipo não suportado ({mime or Path(name).suffix})"})
            continue
        iid = bc.make_item_id(name, f.get("modifiedTime") or f.get("createdTime"))
        size = int(f["size"]) if str(f.get("size", "")).isdigit() else None
        hit = by_drive.get(f.get("id"))
        if not hit and iid in by_id:
            prev = by_id[iid]
            if size is None or prev.get("tamanho") in (None, size):
                hit = prev
        if hit:
            ja.append({"id": f.get("id"), "name": name, "item_id": hit["id"], "status": hit.get("status")})
            continue
        if tipo == "video":
            cmd = f'python scripts/run_ingest.py --file-id {f.get("id")} --name "{name}"'
            if size:
                cmd += f" --size {size}"
            if f.get("modifiedTime"):
                cmd += f' --modified "{f["modifiedTime"]}"'
        else:
            cmd = (f'(baixar pelo conector para work/entrada/) python scripts/run_ingest.py --local "work/entrada/{name}" '
                   f'--drive-id {f.get("id")} --licenca <propria|terceiros|desconhecida> [--fonte "autor, ano, DOI/URL"]')
            if f.get("modifiedTime"):
                cmd += f' --modified "{f["modifiedTime"]}"'
        novos.append({"id": f.get("id"), "name": name, "mimeType": mime, "size": size,
                      "modifiedTime": f.get("modifiedTime"), "item_id": iid, "tipo": tipo, "comando": cmd})

    res = {"novos": novos, "ja_ingeridos": ja, "ignorados": ign}
    if args.out:
        bc.save_json(args.out, res)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    print(f"{len(files)} arquivo(s) no manifesto: {len(novos)} novo(s), {len(ja)} já ingerido(s), {len(ign)} ignorado(s)")
    for n in novos:
        mb = f"{n['size'] / 1e6:.1f} MB" if n["size"] else "?"
        print(f"  NOVO  [{n['tipo']:5}] {n['item_id']:<45} {mb:>10}  {n['name']}")
        print(f"        {n['comando']}")
    for j in ja:
        print(f"  ok    {j['item_id']:<45} ({j.get('status')})  {j['name']}")
    for i in ign:
        print(f"  --    {i.get('name')}: {i['motivo']}")


if __name__ == "__main__":
    main()
