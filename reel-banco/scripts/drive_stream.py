#!/usr/bin/env python3
"""Leitura por faixas (HTTP Range) de um arquivo do Drive compartilhado por link, SEM baixá-lo inteiro.

Por quê: brutos 4K longos (ex.: IMG_4164, 16,8 GB) não cabem no disco de cota fixa e levam muito tempo para baixar
só para tirar 1 min de clipe. O ffmpeg sabe buscar por faixas (lê o índice `moov` e pede só os bytes de cada
trecho), mas não atravessa o proxy HTTPS da sessão nem o formulário de "arquivo grande" do Drive. Este módulo
abre um servidor HTTP LOCAL (127.0.0.1, porta livre) que repassa cada pedido `Range` ao Drive (com `requests`, que
respeita HTTPS_PROXY/REQUESTS_CA_BUNDLE) — o ffmpeg lê `http://127.0.0.1:<porta>/media` como se fosse um arquivo.

Uso como módulo (10_clip.py --stream):
    with DriveStream(file_id, size) as url:   # url = http://127.0.0.1:PORT/media
        subprocess.run(["ffmpeg", "-ss", "1550", "-i", url, ...])
Uso avulso (teste): drive_stream.py <file_id> --probe   -> resolve o link, lê 64 KB do início e 64 KB do fim.

Mesmas regras de segurança do drive_fetch.py: só https em google.com/*.google.com/*.googleusercontent.com;
link não compartilhado -> código 3; resposta inesperada/cota -> código 4.
"""
import argparse
import http.server
import socketserver
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drive_fetch as df  # noqa: E402


def resolve(file_id):
    """Devolve (session, url_final, params, tamanho): a URL que serve os bytes (depois do aviso de arquivo grande)."""
    s = requests.Session()
    s.headers.update(df.UA)
    url, params = df.UC, {"export": "download", "id": file_id, "confirm": "t"}
    s.headers["Range"] = "bytes=0-0"
    r = df.get(s, url, params=params)
    for _ in range(3):
        if r.status_code in (401, 403, 404):
            print(f"ERRO {r.status_code}: arquivo inexistente ou sem permissao", file=sys.stderr); sys.exit(3)
        if not df.is_html(r):
            break
        html = r.text
        if "accounts.google.com" in html[:3000] or "ServiceLogin" in html or "/v3/signin/" in html[:3000]:
            print("ERRO: o Drive pediu login -> arquivo NAO esta compartilhado por link.", file=sys.stderr)
            sys.exit(3)
        action, fields = df.parse_warning(html)
        if not action:
            print("ERRO: pagina HTML inesperada (sem formulario). Possivel cota de download excedida.",
                  file=sys.stderr); sys.exit(4)
        fields.setdefault("confirm", "t")
        url, params = urljoin(r.url, action), fields
        if not df.host_ok(url):
            df.bad_host(url)
        r = df.get(s, url, params=params)
    else:
        print("ERRO: avisos em loop.", file=sys.stderr); sys.exit(4)
    if r.status_code != 206:
        print(f"ERRO: o Drive não aceitou Range (status {r.status_code}) — use o download completo (02_fetch).",
              file=sys.stderr); sys.exit(4)
    total = int(r.headers.get("Content-Range", "/0").rsplit("/", 1)[1])
    final = r.url  # URL efetiva (já com os parâmetros do formulário)
    r.close()
    del s.headers["Range"]
    return s, final, total


CHUNK = 8 << 20  # 8 MB por faixa repassada ao Drive


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silencioso
        pass

    def _upstream(self, rng):
        st = self.server.st
        hdr = {"Range": rng} if rng else {}
        for k in range(6):
            try:
                with st["lock"]:
                    sess, url = st["session"], st["url"]
                r = sess.get(url, headers=hdr, stream=True, timeout=60, allow_redirects=True)
                if r.status_code == 206 and not df.is_html(r):
                    return r
                r.close()
            except requests.RequestException:
                pass
            time.sleep(2 * (k + 1))
            try:  # link expirado: resolve de novo
                s, u, _ = resolve(st["file_id"])
                with st["lock"]:
                    st["session"], st["url"] = s, u
            except SystemExit:
                pass
        return None

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(self.server.st["size"]))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

    def do_GET(self):
        """Repassa o pedido em faixas FECHADAS de CHUNK bytes: o Drive trata "bytes=N-" (aberto) como download
        completo e devolve "Quota exceeded"; faixas fechadas continuam liberadas."""
        size = self.server.st["size"]
        rng = (self.headers.get("Range") or "").strip()
        a, b = 0, size - 1
        if rng.startswith("bytes="):
            lo, _, hi = rng[6:].split(",")[0].partition("-")
            if lo:
                a = int(lo)
                b = min(int(hi), size - 1) if hi else size - 1
            elif hi:  # sufixo: últimos N bytes
                a, b = max(0, size - int(hi)), size - 1
        if a >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.send_header("Content-Length", str(b - a + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        pos = a
        try:
            while pos <= b:
                end = min(b, pos + CHUNK - 1)
                r = self._upstream(f"bytes={pos}-{end}")
                if r is None or r.status_code != 206:
                    if r is not None:
                        r.close()
                    return
                try:
                    for blk in r.iter_content(1 << 20):
                        if blk:
                            self.wfile.write(blk)
                            pos += len(blk)
                            self.server.st["bytes"] += len(blk)
                finally:
                    r.close()
        except (BrokenPipeError, ConnectionResetError, requests.RequestException):
            pass  # o ffmpeg fecha a conexão quando já leu o que precisava


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class DriveStream:
    """Contexto: sobe o servidor local e devolve a URL http://127.0.0.1:PORT/media."""

    def __init__(self, file_id):
        self.file_id = file_id

    def __enter__(self):
        s, url, size = resolve(self.file_id)
        self.httpd = _Server(("127.0.0.1", 0), _Handler)
        self.httpd.st = {"session": s, "url": url, "size": size, "file_id": self.file_id,
                         "lock": threading.Lock(), "bytes": 0}
        self.size = size
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/media"

    @property
    def bytes_read(self):
        return self.httpd.st["bytes"]

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def main():
    ap = argparse.ArgumentParser(description="Servidor local de leitura por faixas de um arquivo do Drive.")
    ap.add_argument("file_id")
    ap.add_argument("--probe", action="store_true", help="lê 64 KB do início e do fim e sai")
    args = ap.parse_args()
    ds = DriveStream(args.file_id)
    with ds as url:
        for rng in ("bytes=0-65535", f"bytes={ds.size - 65536}-{ds.size - 1}"):
            r = requests.get(url, headers={"Range": rng}, proxies={"http": None, "https": None}, timeout=60)
            print(f"{rng}: status {r.status_code}, {len(r.content)} bytes")
        print(f"STREAM OK {args.file_id} tamanho={ds.size} url={url}")


if __name__ == "__main__":
    main()
