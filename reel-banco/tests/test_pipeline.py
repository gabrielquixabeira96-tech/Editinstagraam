#!/usr/bin/env python3
"""Teste de ponta a ponta do reel-banco com material sintético (sem rede, sem custo).

Vídeo: 20 s, 1280×720, 3 cenas de cores diferentes (azul / verde / vermelho, cortes em 7 s e 14 s) + tom
de 220 Hz com pausas. Transcrição: STUB no formato da reel-editor (importado com --import-transcript), porque
um tom não tem fala; com --real-whisper roda o faster-whisper de verdade (só confere que não quebra).
Documentos: um PDF de 2 páginas com uma figura e legenda, e um HTML com 2 seções (Playwright, se houver).

Fluxo: 02 (local) -> 03 (stub) -> 04 -> 05 -> 06 -> segments.json de exemplo -> 06b -> run_ingest --finish
(-> 08) -> 09 -> 10 (+ --broll em um assets/broll/index.json temporário) -> 07 (pdf/html) -> 08 -> 09 --tipo doc.
Usa REEL_BANCO_WORK/REEL_BANCO_INDEX em pasta temporária: o index/ real não é tocado.

Uso:  python tests/test_pipeline.py [--keep] [--real-whisper]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "scripts"
PY = sys.executable

WORDS = ("Olha só, a cricotireoidostomia é a via aérea cirúrgica de emergência quando você não intuba "
         "e não ventila. Só que na criança pequena a gente faz por punção, porque a membrana é minúscula. "
         "Isso muda tudo no plantão.").split()


def sh(*cmd, env=None, check=True, capture=False):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], env=env, text=True, capture_output=capture)
    if check and r.returncode != 0:
        if capture:
            print(r.stdout, r.stderr)
        raise SystemExit(f"FALHOU ({r.returncode}): {' '.join(map(str, cmd))}")
    return r


def make_video(path: Path):
    ff = shutil.which("ffmpeg")
    # 3 cenas de cor (com ruído leve para nitidez > 0) + tom com 2 pausas
    vf = ("color=c=0x2050c0:s=1280x720:r=30:d=7[a];color=c=0x20a050:s=1280x720:r=30:d=7[b];"
          "color=c=0xc03030:s=1280x720:r=30:d=6[c];[a][b][c]concat=n=3:v=1:a=0,noise=alls=12:allf=t[v]")
    af = "sine=f=220:d=20:sample_rate=48000,volume='if(between(t,6.0,6.8)+between(t,13.2,14.0),0,0.4)':eval=frame[aout]"
    sh(ff, "-nostdin", "-v", "error", "-y", "-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[aout]",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "20", str(path))


def stub_transcript(path: Path, src: Path):
    words, t = [], 0.4
    for i, w in enumerate(WORDS):
        if t > 19.4:
            break
        d = 0.18 + 0.03 * len(w)
        words.append({"word": w, "start": round(t, 3), "end": round(t + d, 3), "prob": 0.92 if i % 9 else 0.45})
        t += d + (0.7 if w.endswith(".") else 0.08)
    segs, cur = [], []
    for w in words:
        cur.append(w)
        if w["word"].endswith("."):
            segs.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(x["word"] for x in cur),
                         "avg_logprob": -0.2, "no_speech_prob": 0.01})
            cur = []
    if cur:
        segs.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(x["word"] for x in cur),
                     "avg_logprob": -0.2, "no_speech_prob": 0.01})
    tr = {"source": str(src), "language": "pt", "model": "stub", "duration": 20.0, "generated_by": "tests/stub",
          "refined_boundaries": False, "words": words, "segments": segs}
    path.write_text(json.dumps(tr, ensure_ascii=False, indent=2), encoding="utf-8")
    return tr


def make_pdf(path: Path, img: Path):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 90), "Via aerea cirurgica: revisao", fontsize=18)
    p1.insert_text((72, 130), "A cricotireoidostomia por puncao e preferida em criancas menores de 10 anos.", fontsize=10)
    p1.insert_image(fitz.Rect(72, 160, 372, 330), filename=str(img))
    p1.insert_text((72, 350), "Figura 1. Membrana cricotireoidea e pontos de referencia.", fontsize=9)
    p2 = doc.new_page()
    p2.insert_text((72, 90), "Complicacoes: sangramento, falso trajeto e enfisema subcutaneo.", fontsize=11)
    doc.save(str(path))


HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Atlas teste</title>
<style>body{font-family:sans-serif;margin:24px;background:#fff}h2{color:#0a6}</style></head><body>
<h1>Atlas de via aérea</h1><p>Material próprio para teste.</p>
<h2>Anatomia da membrana</h2><p>A membrana cricotireóidea fica entre a cartilagem tireoide e a cricoide.</p>
<h2>Técnica por punção</h2><p>Na criança pequena a técnica por punção com cateter é a escolha inicial.</p>
</body></html>"""


# ---------------------------------------------------------------------------------------------------------------
# Filtro de "não-fala" do 03_transcribe: no_speech_prob alto só descarta se a ENERGIA for de silêncio/ruído.
# O whisper é substituído por um módulo falso (faster_whisper/ no PYTHONPATH) que devolve segmentos fixos —
# o mesmo que o whisper real faz em fala com eco/voz baixa (no_speech alto) e em silêncio (alucinação).
# ---------------------------------------------------------------------------------------------------------------
FAKE_FW = '''
import json, os
class _O:
    def __init__(self, **k): self.__dict__.update(k)
class WhisperModel:
    def __init__(self, *a, **k): pass
    def transcribe(self, audio, **kw):
        spec = json.load(open(os.environ["FAKE_WHISPER_SPEC"]))
        segs = []
        for s in spec:
            ws = [_O(word=" " + w, start=s["start"] + i * s["step"], end=s["start"] + i * s["step"] + s["step"] * 0.8,
                     probability=0.9) for i, w in enumerate(s["text"].split())]
            segs.append(_O(start=s["start"], end=s["start"] + len(ws) * s["step"], text=s["text"], avg_logprob=-0.3,
                           no_speech_prob=s["ns"], words=ws))
        return iter(segs), _O(language="pt")
'''

# trechos (s): fala sintética (tom com harmônicos modulado em sílabas) 2–10 e 22–26; silêncio puro 11–21
FAKE_SPEC = [
    {"start": 2.2, "step": 0.5, "ns": 0.85, "text": "isso aqui é fala de verdade com eco que o whisper acha que não é fala"},
    {"start": 12.0, "step": 0.6, "ns": 0.92, "text": "obrigado por assistir e até a próxima"},
    {"start": 22.2, "step": 0.45, "ns": 0.05, "text": "cricotireoidostomia, intubação, via aérea, plantão,"},
]


def speechlike(t, a=0.25):
    import numpy as np
    car = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((140, 280, 420, 700), 1))
    env = np.abs(np.sin(2 * np.pi * 2.5 * t)) ** 0.7  # ~5 "sílabas" por segundo
    return a * env * car / 2.1


def write_wav(path, x, sr=16000):
    import wave
    import numpy as np
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def test_no_speech_filter(tmp: Path, env):
    import importlib.util
    import numpy as np
    sr = 16000
    t = np.arange(int(28 * sr)) / sr
    x = np.zeros_like(t)
    for a0, a1 in ((2.0, 10.0), (22.0, 26.0)):
        m = (t >= a0) & (t < a1)
        x[m] = speechlike(t[m])
    wav = tmp / "fala_e_silencio.wav"
    write_wav(wav, x, sr)
    fake = tmp / "fakefw" / "faster_whisper"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(FAKE_FW)
    spec = tmp / "fake_spec.json"
    spec.write_text(json.dumps(FAKE_SPEC, ensure_ascii=False))
    e2 = dict(env, PYTHONPATH=str(tmp / "fakefw"), FAKE_WHISPER_SPEC=str(spec))
    r = sh(PY, S / "02_fetch.py", "--local", wav, "--drive-id", "FAKEWAV", "--modified", "2025-10-23T00:00:00Z",
           env=e2, capture=True)
    iid = next(l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("ITEM_ID="))
    wdir = tmp / "work" / iid
    sh(PY, S / "03_transcribe.py", iid, env=e2)
    tr = json.loads((wdir / "transcript.json").read_text())
    got = " ".join(w["word"] for w in tr["words"])
    print("palavras mantidas:", got)
    dsc = tr["descartes"]
    print("descartes:", {k: v for k, v in dsc.items() if k != "detalhe"})
    assert got == FAKE_SPEC[0]["text"], "a fala (no_speech alto, mas com energia) tem de ser mantida inteira"
    assert "obrigado" not in got and "cricotireoidostomia" not in got
    assert dsc["por_motivo"] == {"silencio": 7, "prompt": 5} and dsc["total"] == 12, dsc
    assert dsc["mantidas_no_speech_alto"] == 16 and dsc["regra_antiga_descartaria"] == 23, dsc
    assert {d["motivo"] for d in dsc["detalhe"]} == {"silencio", "prompt"}
    assert any(s.get("no_speech_mantido") for s in tr["segments"])

    # sem --replace, nunca retranscreve; com --replace: backup + correções de 'word' reaplicadas
    tr["words"][3]["word"] = "FALA"  # "correção manual"
    (wdir / "transcript.json").write_text(json.dumps(tr, ensure_ascii=False, separators=(",", ":")))
    r = sh(PY, S / "03_transcribe.py", iid, env=e2, capture=True)
    assert "não vou transcrever de novo" in r.stdout, r.stdout
    sh(PY, S / "03_transcribe.py", iid, "--replace", env=e2)
    tr2 = json.loads((wdir / "transcript.json").read_text())
    assert (wdir / "transcript.v1.json").exists() and (wdir / "transcript.v1.txt").exists()
    assert tr2["words"][3]["word"] == "FALA" and len(tr2["words"]) == 16, tr2["words"][:5]
    assert tr2["substitui"]["backup"] == "transcript.v1.json" and len(tr2["substitui"]["edicoes_reaplicadas"]) == 1
    sh(PY, S / "03_transcribe.py", iid, "--replace", "--no-carry-edits", env=e2)
    tr3 = json.loads((wdir / "transcript.json").read_text())
    assert (wdir / "transcript.v2.json").exists() and tr3["words"][3]["word"] == "fala"

    # piso RELATIVO: arquivo com ruído de fundo constante (-50 dBFS) — alucinação no ruído sai, fala fica
    spec_m = importlib.util.spec_from_file_location("transcribe03", S / "03_transcribe.py")
    m = importlib.util.module_from_spec(spec_m)
    spec_m.loader.exec_module(m)
    rng = np.random.default_rng(7)
    xn = x + rng.normal(0, 10 ** (-50 / 20), len(x))
    wav2 = tmp / "fala_e_ruido.wav"
    write_wav(wav2, xn, sr)
    sys.path.insert(0, str(tmp / "fakefw"))
    os.environ["FAKE_WHISPER_SPEC"] = str(spec)
    from faster_whisper import WhisperModel as Fake
    args = argparse.Namespace(window=600.0, overlap=1.5, lang="pt", beam_size=5, no_vad=False, prompt=m.DEFAULT_PROMPT,
                              no_speech=0.6, silence_margin=6.0, abs_silence=-70.0, no_refine=True)
    words, segs, dsc2 = m.transcribe_windows(Fake(), wav2, 28.0, args)
    print("ruído de fundo:", {k: v for k, v in dsc2.items() if k != "detalhe"})
    assert -60 < dsc2["piso_db"] < -45, dsc2["piso_db"]
    assert " ".join(w["word"] for w in words) == FAKE_SPEC[0]["text"]
    assert dsc2["por_motivo"] == {"silencio": 7, "prompt": 5}
    assert m.prompt_leak_mask(["a", "cirurgia", "e", "anatomia"], m.prompt_terms(m.DEFAULT_PROMPT)) == [False] * 4
    print("filtro de não-fala OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--real-whisper", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="reel_banco_test_"))
    env = dict(os.environ, REEL_BANCO_WORK=str(tmp / "work"), REEL_BANCO_INDEX=str(tmp / "index"),
               PYTHONWARNINGS="ignore")
    print(f"pasta temporária: {tmp}")
    try:
        vid = tmp / "Aula Sintética Vias Aéreas.mp4"
        make_video(vid)
        os.utime(vid, (1761177600, 1761177600))  # 2025-10-23 -> item_id previsível
        sh(PY, S / "00_check_env.py", env=env, check=False)

        # 01_scan com manifesto
        man = tmp / "manifest.json"
        man.write_text(json.dumps([{"id": "FAKE1", "name": vid.name, "mimeType": "video/mp4", "size": str(vid.stat().st_size),
                                    "modifiedTime": "2025-10-23T00:00:00Z"},
                                   {"id": "F2", "name": "planilha.xlsx", "mimeType": "application/vnd.ms-excel"}]))
        r = sh(PY, S / "01_scan.py", "--manifest", man, "--json", env=env, capture=True)
        scan = json.loads(r.stdout)
        assert len(scan["novos"]) == 1 and len(scan["ignorados"]) == 1, scan
        iid = scan["novos"][0]["item_id"]
        assert iid == "20251023_aula-sintetica-vias-aereas", iid

        # 02 local
        r = sh(PY, S / "02_fetch.py", "--local", vid, "--drive-id", "FAKE1", "--modified", "2025-10-23T00:00:00Z",
               env=env, capture=True)
        assert f"ITEM_ID={iid}" in r.stdout, r.stdout
        wdir = tmp / "work" / iid
        item = json.loads((wdir / "item.json").read_text())
        assert item["tipo"] == "video" and abs(item["duracao"] - 20) < 0.2 and item["sha256"]

        # 03 transcrição
        if a.real_whisper:
            sh(PY, S / "03_transcribe.py", iid, env=env)
            assert (wdir / "transcript.json").exists()
            (wdir / "transcript.json").unlink()
        stub = tmp / "stub_transcript.json"
        stub_transcript(stub, vid)
        sh(PY, S / "03_transcribe.py", iid, "--import-transcript", stub, env=env)
        r = sh(PY, S / "03_transcribe.py", iid, env=env, capture=True)  # não pode retranscrever
        assert "não vou transcrever de novo" in r.stdout, r.stdout
        txt = (wdir / "transcript.txt").read_text()
        assert "── [00:00" in txt and "(?)" in txt

        # 04 cenas
        sh(PY, S / "04_scenes.py", iid, env=env)
        scenes = json.loads((wdir / "scenes.json").read_text())["scenes"]
        print("cenas:", [(s["t0"], s["t1"]) for s in scenes])
        assert len(scenes) == 3, scenes
        assert abs(scenes[1]["t0"] - 7.0) < 0.2 and abs(scenes[2]["t0"] - 14.0) < 0.2
        assert all(s["audio_db"] is not None for s in scenes)
        # cache
        r = sh(PY, S / "04_scenes.py", iid, env=env, capture=True)
        assert "cache" in r.stdout

        # 05 keyframes
        sh(PY, S / "05_keyframes.py", iid, "--every", "6", env=env)
        idx = tmp / "index" / "items" / iid
        thumbs = list((idx / "thumbs").glob("*.jpg"))
        assert (idx / "contact_sheet.jpg").exists() and len(thumbs) >= 3
        from PIL import Image
        assert Image.open(thumbs[0]).width == 320

        # 06 segment_input
        sh(PY, S / "06_segment_input.py", iid, "--block", "10", env=env)
        si = (wdir / "segment_input.md").read_text()
        assert "### Bloco 2" in si and "cricotireoidostomia" in si and "contact_sheet.jpg" in si

        # segments.json "escrito pelo Claude"
        segs = {"item_id": iid, "segments": [
            {"id": "x", "t0": 0.3, "t1": 7.2, "tema": "cricotireoidostomia como via aérea de emergência",
             "tags": ["via-aerea", "cricotireoidostomia", "Emergência"],
             "tese": "quando não intuba e não ventila, a via é cirúrgica",
             "citacao": "quando você não intuba e não ventila", "gancho_potencial": 4,
             "publicos": ["plantonista", "residente"], "qualidade_visual": 3, "qualidade_audio": 4,
             "usos": ["fala-principal", "citacao-card"], "notas": "abertura forte"},
            {"id": "y", "t0": 7.5, "t1": 13.0, "tema": "punção na criança pequena", "tags": ["pediatria", "puncao"],
             "tese": "na criança a membrana é minúscula, então punção", "citacao": "na criança pequena a gente faz por punção",
             "gancho_potencial": 3, "publicos": ["candidato-residencia"], "qualidade_visual": 3,
             "qualidade_audio": 4, "usos": ["corte-rapido", "broll"], "notas": ""}]}
        (wdir / "segments.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        # citação errada tem de reprovar
        bad = json.loads(json.dumps(segs))
        bad["segments"][0]["citacao"] = "frase que não existe na fala de jeito nenhum aqui"
        (wdir / "segments.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        r = sh(PY, S / "06b_validate_segments.py", iid, env=env, check=False, capture=True)
        assert r.returncode != 0, "validador deveria reprovar citação inexistente"
        # defeitos de esquema que têm de reprovar: tema/tese vazios, publicos [], citação vazia fora de broll,
        # segmento que não é objeto, JSON malformado
        def must_fail(mut, why):
            b2 = json.loads(json.dumps(segs))
            mut(b2)
            (wdir / "segments.json").write_text(json.dumps(b2, ensure_ascii=False), encoding="utf-8")
            rr = sh(PY, S / "06b_validate_segments.py", iid, env=env, check=False, capture=True)
            assert rr.returncode != 0 and "Traceback" not in rr.stderr, (why, rr.stderr[-400:])
        must_fail(lambda b2: b2["segments"][0].__setitem__("tema", " "), "tema vazio")
        must_fail(lambda b2: b2["segments"][0].__setitem__("tese", ""), "tese vazia")
        must_fail(lambda b2: b2["segments"][0].__setitem__("publicos", []), "publicos vazio")
        must_fail(lambda b2: b2["segments"][0].__setitem__("citacao", ""), "citação vazia em fala-principal")
        must_fail(lambda b2: b2["segments"].append("texto solto"), "segmento não-objeto")
        (wdir / "segments.json").write_text("{ isto não é json", encoding="utf-8")
        rr = sh(PY, S / "06b_validate_segments.py", iid, env=env, check=False, capture=True)
        assert rr.returncode != 0 and "Traceback" not in rr.stderr and "JSON" in rr.stderr, rr.stderr[-300:]
        # citação vazia é aceita quando usos == ["broll"]; ids "#sXYZ" são renumerados
        ok2 = json.loads(json.dumps(segs))
        ok2["segments"][1].update({"citacao": "", "usos": ["broll"], "id": f"{iid}#sXYZ"})
        ok2["segments"][0]["id"] = f"{iid}#s01"
        (wdir / "segments.json").write_text(json.dumps(ok2, ensure_ascii=False), encoding="utf-8")
        sh(PY, S / "06b_validate_segments.py", iid, env=env)
        v2 = json.loads((wdir / "segments.json").read_text())["segments"]
        assert [x["id"] for x in v2] == [f"{iid}#s01", f"{iid}#s02"], [x["id"] for x in v2]
        (wdir / "segments.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")

        # finish (06b + cópia + 08)
        sh(PY, S / "run_ingest.py", "--finish", iid, env=env)
        v = json.loads((idx / "segments.json").read_text())["segments"]
        assert v[0]["id"] == f"{iid}#s01" and v[1]["id"] == f"{iid}#s02"
        assert v[0]["t0"] >= 0.3 and "citacao_t0" in v[0] and "emergencia" in v[0]["tags"]
        assert (idx / "transcript.json").exists()
        assert (tmp / "index" / "index.md").exists() and (tmp / "index" / "bm25_tokens.json").exists()
        md = (tmp / "index" / "index.md").read_text()
        assert f"{iid}#s01" in md

        # 09 busca
        r = sh(PY, S / "09_search.py", "cricotireoidostomia emergência", "--json", env=env, capture=True)
        res = json.loads(r.stdout)
        assert res and res[0]["id"] == f"{iid}#s01", res[:1]
        r = sh(PY, S / "09_search.py", "criança punção", "--publico", "candidato-residencia", "--json", env=env, capture=True)
        res = json.loads(r.stdout)
        assert res and res[0]["id"] == f"{iid}#s02", res[:1]
        sh(PY, S / "09_search.py", "via aerea", "--show-text", env=env)

        # 10 clip + broll
        bidx = tmp / "reel-director" / "assets" / "broll" / "index.json"
        r = sh(PY, S / "10_clip.py", f"{iid}#s02", "--broll", "--broll-index", bidx, env=env, capture=True)
        clip = Path(next(l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("CLIP=")))
        pr = sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration",
                "-of", "json", clip, capture=True)
        info = json.loads(pr.stdout)
        codecs = {s["codec_name"] for s in info["streams"]}
        d = float(info["format"]["duration"])
        print("clipe:", clip, codecs, d)
        assert {"h264", "aac"} <= codecs and 4.5 < d < 7.5
        b = json.loads(bidx.read_text())
        assert b[0]["file"].startswith("assets/broll/") and b[0]["segment_id"] == f"{iid}#s02"
        assert (bidx.parent / clip.name).exists()
        ct = json.loads(clip.with_suffix(".transcript.json").read_text())
        assert ct["words"] and ct["words"][0]["start"] < 1.0 and ct["duration"] < 7.5, ct["words"][:2]
        r = sh(PY, S / "10_clip.py", f"{iid}#s01", "--precise", "--for-editor", tmp / "editor_work", env=env, capture=True)
        clip1 = Path(next(l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("CLIP=")))
        d1 = float(json.loads(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", clip1,
                                  capture=True).stdout)["format"]["duration"])
        assert 6.5 < d1 < 7.6, d1
        et = tmp / "editor_work" / clip1.stem / "transcript.json"
        assert et.exists() and json.loads(et.read_text())["words"][0]["word"] == "Olha"
        assert "\n" not in (wdir / "transcript.json").read_text().strip(), "transcript.json deve ser compacto"
        # sem pickle no índice
        assert not (tmp / "index" / "bm25.pkl").exists()

        # 07 documentos
        img = tmp / "fig.png"
        Image.new("RGB", (600, 340), (200, 120, 90)).save(img)
        pdf = tmp / "Revisão Via Aérea.pdf"
        make_pdf(pdf, img)
        r = sh(PY, S / "run_ingest.py", "--local", pdf, "--licenca", "terceiros", "--fonte", "Teste et al., 2026",
               env=env, capture=True)
        print(r.stdout)
        did = next(l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("ITEM_ID="))
        figs = json.loads((tmp / "work" / did / "figuras.json").read_text())
        assert figs and figs[0]["pagina"] == 1 and "Figura 1" in figs[0]["legenda"], figs
        dsegs = {"item_id": did, "segments": [
            {"id": "a", "t0": None, "t1": None, "ref": {"pagina": 1, "figura": figs[0]["id"]},
             "tema": "punção na criança abaixo de 10 anos", "tags": ["pediatria", "puncao", "via-aerea"],
             "tese": "abaixo de 10 anos, punção", "citacao": "A cricotireoidostomia por puncao e preferida em criancas menores de 10 anos.",
             "gancho_potencial": 2, "publicos": ["residente"], "qualidade_visual": 3, "qualidade_audio": 0,
             "usos": ["citacao-card", "broll"], "notas": "terceiros: creditar"}]}
        (tmp / "work" / did / "segments.json").write_text(json.dumps(dsegs, ensure_ascii=False))
        sh(PY, S / "run_ingest.py", "--finish", did, env=env)
        hp = tmp / "atlas.html"
        hp.write_text(HTML, encoding="utf-8")
        r = sh(PY, S / "07_doc_ingest.py", "--local", hp, "--licenca", "propria", env=env, capture=True)
        print(r.stdout[-600:])
        hid = next(l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("ITEM_ID="))
        dt = json.loads((tmp / "work" / hid / "doc_text.json").read_text())
        assert any("punção" in p["texto"] for p in dt["partes"]), dt
        r = sh(PY, S / "09_search.py", "punção criança", "--tipo", "doc", "--json", env=env, capture=True)
        res = json.loads(r.stdout)
        assert res and res[0]["item_id"] == did and res[0]["licenca"] == "terceiros"
        r = sh(PY, S / "10_clip.py", f"{did}#s01", env=env, capture=True)
        assert "CLIP=" in r.stdout and r.stdout.strip().endswith(".png")
        # sessão nova: work/ do documento sumiu -> erro legível com instrução de reabastecer, e depois reextração
        shutil.rmtree(tmp / "work" / did)
        r = sh(PY, S / "10_clip.py", f"{did}#s01", env=env, check=False, capture=True)
        assert r.returncode != 0 and "Reabasteça" in r.stderr and "Traceback" not in r.stderr, r.stderr[-400:]
        sh(PY, S / "07_doc_ingest.py", "--local", pdf, "--item-id", did, "--licenca", "terceiros",
           "--fonte", "Teste et al., 2026", env=env)
        r = sh(PY, S / "10_clip.py", f"{did}#s01", env=env, capture=True)
        assert r.stdout.strip().endswith(".png")
        assert json.loads((tmp / "index" / "items" / did / "item.json").read_text())["status"] == "indexado"
        sh(PY, S / "08_index.py", env=env)
        full = json.loads((tmp / "index" / "index.json").read_text())
        assert full["n_itens"] == 2 and full["n_segmentos"] == 3 and hid in full["pendentes"]

        test_no_speech_filter(tmp, env)
        print("\nTESTE OK")
    finally:
        if a.keep:
            print(f"artefatos mantidos em {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
