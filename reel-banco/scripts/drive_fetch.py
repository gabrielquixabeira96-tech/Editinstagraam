#!/usr/bin/env python3
"""PODE SER SUBSTITUÍDO: implementação provisória (validada em 2026-10-23) — mantenha a interface
`drive_fetch.py <file_id> <destino> [--expect-size N]` e os códigos de saída se trocar por outra.

Baixa um arquivo do Google Drive compartilhado por link ("qualquer pessoa com o link").

Uso: drive_fetch.py <file_id> <destino> [--expect-size BYTES]

- Trata o aviso de "arquivo grande / sem verificacao de virus" (formulario download-form
  em drive.usercontent.google.com) extraindo action + campos ocultos (id, export, confirm, uuid).
- Detecta arquivo NAO compartilhado (Drive devolve a pagina de login do Google) e sai com codigo 3.
- Grava em <destino>.part e renomeia so ao final; confere tamanho com Content-Length
  e, se dado, com --expect-size (tamanho do get_file_metadata do MCP).
- Nao segue redirecionamentos automaticamente: segue Location a mao (max. 5) e aceita so https em google.com,
  *.google.com e *.googleusercontent.com (mesma checagem no action do formulario); host fora disso -> 4.
Saida: 0 ok; 2 uso; 3 nao compartilhado/sem permissao; 4 resposta inesperada (HTML sem formulario, host nao
permitido, 5xx, erro de rede, redirecionamentos demais); 5 tamanho divergente.
Respeita HTTPS_PROXY e REQUESTS_CA_BUNDLE/SSL_CERT_FILE do ambiente (requests faz isso sozinho).
"""
import argparse, hashlib, os, sys, time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import requests

MAX_REDIRECTS = 5
ALLOWED_SUFFIXES = (".google.com", ".googleusercontent.com")

UC = "https://drive.google.com/uc"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) drive_fetch/1.0"}


class FormParser(HTMLParser):
    """Extrai o <form id="download-form"> (ou o primeiro form com action de download)."""
    def __init__(self):
        super().__init__()
        self.action, self.fields, self._in = None, {}, False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and self.action is None and (
                a.get("id") == "download-form" or "download" in (a.get("action") or "")):
            self.action, self._in = a.get("action"), True
        elif tag == "input" and self._in and a.get("name"):
            self.fields[a["name"]] = a.get("value", "")
    def handle_endtag(self, tag):
        if tag == "form":
            self._in = False


def parse_warning(html):
    p = FormParser(); p.feed(html)
    return p.action, p.fields


def is_html(resp):
    return "text/html" in resp.headers.get("Content-Type", "")


def host_ok(url):
    """Só https em google.com, *.google.com e *.googleusercontent.com."""
    u = urlparse(url)
    h = (u.hostname or "").lower()
    return u.scheme == "https" and (h == "google.com" or h.endswith(ALLOWED_SUFFIXES))


def bad_host(url):
    print(f"ERRO: redirecionamento/formulário para host não permitido: {urlparse(url).scheme}://"
          f"{urlparse(url).hostname} (só https em google.com/*.google.com/*.googleusercontent.com)", file=sys.stderr)
    sys.exit(4)


def get(s, url, params=None):
    """GET sem seguir redirecionamentos automaticamente: segue Location à mão (máx. 5), validando cada host.
    Erros de rede/HTTP 5xx -> código 4 (sem traceback)."""
    if not host_ok(url):
        bad_host(url)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            r = s.get(url, params=params, stream=True, allow_redirects=False, timeout=60)
        except requests.RequestException as e:
            print(f"ERRO de rede: {e}", file=sys.stderr); sys.exit(4)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location")
            r.close()
            if not loc:
                print("ERRO: redirecionamento sem Location", file=sys.stderr); sys.exit(4)
            url = urljoin(r.url, loc)
            params = None
            if not host_ok(url):
                bad_host(url)
            continue
        if r.status_code >= 500:
            print(f"ERRO {r.status_code}: servidor do Drive indisponível; tente mais tarde", file=sys.stderr)
            sys.exit(4)
        return r
    print(f"ERRO: mais de {MAX_REDIRECTS} redirecionamentos", file=sys.stderr); sys.exit(4)


def fetch(file_id, dest, expect_size=None, chunk=1 << 20):
    s = requests.Session(); s.headers.update(UA)
    r = get(s, UC, params={"export": "download", "id": file_id, "confirm": "t"})
    for _ in range(3):  # ate 3 paginas de aviso encadeadas
        if r.status_code in (401, 403, 404):
            print(f"ERRO {r.status_code}: arquivo inexistente ou sem permissao", file=sys.stderr); sys.exit(3)
        if not is_html(r):
            break
        html = r.text
        if "accounts.google.com" in html[:3000] or "ServiceLogin" in html or "/v3/signin/" in html[:3000]:
            print("ERRO: o Drive pediu login -> arquivo NAO esta compartilhado por link "
                  "('Qualquer pessoa com o link: Leitor').", file=sys.stderr); sys.exit(3)
        action, fields = parse_warning(html)
        if not action:
            print("ERRO: pagina HTML inesperada (sem formulario de download). "
                  "Possivel cota de download excedida.", file=sys.stderr)
            print(html[:500], file=sys.stderr); sys.exit(4)
        fields.setdefault("confirm", "t")
        action = urljoin(r.url, action)
        if not host_ok(action):
            bad_host(action)
        r = get(s, action, params=fields)
    else:
        print("ERRO: avisos em loop.", file=sys.stderr); sys.exit(4)
    if r.status_code >= 400:
        print(f"ERRO {r.status_code}: resposta inesperada do Drive", file=sys.stderr); sys.exit(4)

    total = int(r.headers.get("Content-Length") or 0) or None
    part = dest + ".part"
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    h, got, t0, last = hashlib.sha256(), 0, time.time(), 0.0
    try:
        with open(part, "wb") as f:
            for b in r.iter_content(chunk):
                if not b:
                    continue
                f.write(b); h.update(b); got += len(b)
                now = time.time()
                if now - last >= 1 or (total and got == total):
                    last = now
                    pct = f"{100*got/total:5.1f}%" if total else "  ?  "
                    mbps = got / max(now - t0, 1e-6) / 1e6
                    print(f"\r{pct} {got/1e6:9.1f} MB  {mbps:6.1f} MB/s", end="", file=sys.stderr, flush=True)
    except requests.RequestException as e:
        print(f"\nERRO de rede durante o download: {e} (parcial mantido em {part})", file=sys.stderr); sys.exit(4)
    print(file=sys.stderr)
    for label, want in (("Content-Length", total), ("--expect-size", expect_size)):
        if want is not None and got != want:
            print(f"ERRO: tamanho {got} != {label} {want}; mantido {part}", file=sys.stderr); sys.exit(5)
    os.replace(part, dest)
    print(f"OK {dest} bytes={got} sha256={h.hexdigest()} tempo={time.time()-t0:.1f}s")
    return got, h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file_id"); ap.add_argument("dest")
    ap.add_argument("--expect-size", type=int)
    a = ap.parse_args()
    fetch(a.file_id, a.dest, a.expect_size)


if __name__ == "__main__":
    main()
