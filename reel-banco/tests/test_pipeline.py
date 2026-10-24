#!/usr/bin/env python3
"""Teste de ponta a ponta do reel-banco com material sintético (sem rede, sem custo).

Vídeo: 20 s, 1280×720, 3 cenas de cores diferentes (azul / verde / vermelho, cortes em 7 s e 14 s) + tom
de 220 Hz com pausas. Transcrição: STUB no formato da reel-editor (importado com --import-transcript), porque
um tom não tem fala; com --real-whisper roda o faster-whisper de verdade (só confere que não quebra).
Documentos: um PDF de 2 páginas com uma figura e legenda, e um HTML com 2 seções (Playwright, se houver).

Fluxo: 02 (local) -> 03 (stub) -> 04 -> 05 -> 06 -> segments.json de exemplo -> 06b -> run_ingest --finish
(-> 08) -> 09 -> 10 (+ --broll em um assets/broll/index.json temporário) -> 07 (pdf/html) -> 08 -> 09 --tipo doc.
Usa REEL_BANCO_WORK/REEL_BANCO_INDEX em pasta temporária: o index/ real não é tocado.

Também testa a reaplicação de correções manuais no `03_transcribe.py --replace`: (a) whisper falso com uma
segunda execução deslocada 20–40 ms e com pontuação/caixa diferentes; (b) regressão com o par REAL do
IMG_3502 (transcript.v1.json -> saída bruta nova), congelado em tests/fixtures/img3502_replace.json.gz.

Uso:  python tests/test_pipeline.py [--keep] [--real-whisper]
      python tests/test_pipeline.py --rebuild-3502   (recria a fixture; precisa de work/banco/<3502>/transcript.v1.json)
"""
import argparse
import copy
import gzip
import importlib.util
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
            # "shift" + "jit"·(i % 3): deslocamento por palavra, como entre duas execuções reais do whisper
            d = lambda i: s.get("shift", 0.0) + s.get("jit", 0.0) * (i % 3)
            ws = [_O(word=" " + w, start=s["start"] + i * s["step"] + d(i),
                     end=s["start"] + i * s["step"] + s["step"] * 0.8 + d(i),
                     probability=0.9) for i, w in enumerate(s["text"].split())]
            segs.append(_O(start=s["start"] + d(0), end=s["start"] + len(ws) * s["step"] + d(0), text=s["text"],
                           avg_logprob=-0.3,
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


def load_t03():
    spec_m = importlib.util.spec_from_file_location("transcribe03", S / "03_transcribe.py")
    m = importlib.util.module_from_spec(spec_m)
    spec_m.loader.exec_module(m)
    return m


def test_no_speech_filter(tmp: Path, env):
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
    assert all(w["raw"] == w["word"] for w in tr3["words"]), "toda palavra nova guarda o texto do whisper em 'raw'"

    # --replace com whisper NÃO idêntico: 2ª execução deslocada 20–40 ms, pontuação/caixa diferentes em palavras
    # corrigidas e não corrigidas. Correções reais (inclusive só de caixa/pontuação) voltam; diferença entre
    # execuções nunca vira "correção".
    human = {1: "ali", 3: "FALA", 5: "verdade,", 11: "jura"}  # 'aqui' 'fala' 'verdade' 'acha'
    for i, wd in human.items():
        tr3["words"][i]["word"] = wd
    (wdir / "transcript.json").write_text(json.dumps(tr3, ensure_ascii=False, separators=(",", ":")))
    spec2 = tmp / "fake_spec_run2.json"
    run2 = copy.deepcopy(FAKE_SPEC)
    run2[0].update(shift=0.02, jit=0.01,
                   text="Isso aqui, é fala de verdade com eco que o Whisper Acha que não é fala.")
    spec2.write_text(json.dumps(run2, ensure_ascii=False))
    r = sh(PY, S / "03_transcribe.py", iid, "--replace", env=dict(e2, FAKE_WHISPER_SPEC=str(spec2)), capture=True)
    print(r.stdout[-700:])
    tr4 = json.loads((wdir / "transcript.json").read_text())
    got4 = [w["word"] for w in tr4["words"]]
    want4 = "Isso ali, é FALA de verdade, com eco que o Whisper jura que não é fala.".split()
    assert got4 == want4, got4
    assert [w["raw"] for w in tr4["words"]] == run2[0]["text"].split(), "raw = texto da execução nova"
    sub4 = tr4["substitui"]
    assert sorted(e["i"] for e in sub4["edicoes_reaplicadas"]) == sorted(human), sub4["edicoes_reaplicadas"]
    assert sub4["correcoes_detectadas"] == 4 and sub4["nao_reaplicadas"] == [] and sub4["ja_corretas"] == []
    assert sub4["contagem"] == {"detectadas": 4, "reaplicadas": 4, "ja_corretas": 0, "nao_reaplicadas": 0}
    assert tr4["janelas"] == {"window": 600.0, "overlap": 1.5}
    assert not any(e["i"] in (0, 10, 15) for e in sub4["edicoes_reaplicadas"]), "diferença espúria virou correção"
    # a regra antiga (start/end idênticos ao ms) não casaria nenhuma destas correções
    old_keys = {(w["start"], w["end"]) for w in tr3["words"]}
    assert not any((w["start"], w["end"]) in old_keys for w in tr4["words"])

    # mesmo cenário com transcript ANTIGO sem "raw" (anterior a 2026-10-24): o bruto sai do texto dos segmentos
    m = load_t03()
    legacy = copy.deepcopy(tr3["words"])
    for w in legacy:
        w.pop("raw", None)
    fresh = [dict(w, word=w["raw"]) for w in tr4["words"]]
    c = m.carry_word_edits(fresh, legacy, tr3["segments"])
    assert c["fonte"] == "segments" and c["detectadas"] == 4 and not c["nao_reaplicadas"], c
    assert [w["word"] for w in fresh] == want4, [w["word"] for w in fresh]
    # fusão manual (bruto com 2 palavras) e palavra sem bruto: vão para nao_reaplicadas, sem mexer no novo
    merged = copy.deepcopy(tr3["words"])
    merged[13:15] = [dict(merged[13], word="né", raw="não é", end=merged[14]["end"])]
    fresh2 = [dict(w, word=w["raw"]) for w in tr4["words"]]
    c2 = m.carry_word_edits(fresh2, merged, tr3["segments"])
    assert [(r["i_antigo"], r["motivo"]) for r in c2["nao_reaplicadas"]] == [(13, "estrutural")], c2["nao_reaplicadas"]
    assert [w["word"] for w in fresh2][13:15] == ["não", "é"]
    print("reaplicação tolerante de correções (whisper falso) OK")

    # piso RELATIVO: arquivo com ruído de fundo constante (-50 dBFS) — alucinação no ruído sai, fala fica
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


# ---------------------------------------------------------------------------------------------------------------
# Regressão com o par REAL do IMG_3502 (2026-10-24): v1 (28 correções humanas, sem "raw") -> retranscrição em que
# os tempos deslocaram ~20 ms e algumas palavras mudaram. A regra antiga (start/end idênticos) casou 9 das 28 e
# "reaplicou" 30 diferenças entre execuções; 19 foram refeitas à mão (registro em index/.../transcript.json →
# substitui.correcoes_manuais_pos_replace).
# ---------------------------------------------------------------------------------------------------------------
ITEM_3502 = "20261023_img-3502"
FIX_3502 = ROOT / "tests" / "fixtures" / "img3502_replace.json.gz"
# (índice no v1, índice na saída bruta nova, "word" final escolhido à mão) — todas as 28 correções do v1
EXPECT_3502 = [
    (9, 11, "stromal"), (77, 82, "GIST"), (146, 150, "morbidade"), (243, 325, "tumores"),
    (244, 326, "pancreáticos,"), (247, 329, "GIST."), (301, 381, "atende"), (309, 388, "GIST,"),
    (565, 702, "mantra,"), (610, 748, "desfecho"), (678, 987, "indescritível,"), (1055, 1524, "polipose"),
    (1057, 1526, "adenomatosa"), (1064, 1533, "Lynch"), (1110, 1578, "polipose"), (1111, 1579, "adenomatosa"),
    (1116, 1584, "proctocolectomia"), (1173, 1638, "atendi"), (1185, 1649, "Lynch,"), (1202, 1666, "oncogenético"),
    (1222, 1684, "mutação"), (1224, 1686, "MLH1"), (1226, 1688, "MSH2")]
ALREADY_OK_3502 = [(7, 9, "GIST"), (575, 712, "especializando,")]   # o whisper novo já acertou
# fusões feitas à mão na versão nova ('é indicando,' -> 'Carney,'; 'antep53,' -> 'TP53,'): não automatizáveis
STRUCTURAL_3502 = [240, 1149, 1150]
# as 9 que a regra antiga casou; as outras 19 do v1 são as refeitas à mão
OLD_RULE_3502 = {146, 243, 301, 1055, 1057, 1111, 1185, 1202, 1222}


def _slim(w):
    return {k: w[k] for k in ("word", "start", "end", "raw_start", "raw_end") if k in w}


def build_3502_fixture():
    """Congela o par real (precisa do backup em work/, que não vai para o git):
    v1 = work/banco/<id>/transcript.v1.json, palavras e segmentos como estão (sem "raw": as correções saem do
    texto dos segmentos, que nunca é editado); saída bruta nova = index/items/<id>/transcript.json com "word"
    revertido para o texto do whisper pelos registros de substitui ("whisper" de edicoes_reaplicadas e de
    correcoes_manuais_pos_replace.edicoes) e a fusão desfeita ('Carney,' -> 'é' + 'indicando,'; o fim do 'é' vem
    do registro). Exceção documentada na própria nota: nas 2 letras soletradas de 13:51–13:56 a reaplicação
    automática foi desfeita, então o "whisper" registrado é o texto do v1 e o cru novo é o texto atual ('H,',
    'I,'), o que os segmentos confirmam. Confere a reconstrução contra o texto dos segmentos."""
    m = load_t03()
    v1 = json.loads((ROOT / "work" / "banco" / ITEM_3502 / "transcript.v1.json").read_text(encoding="utf-8"))
    cur = json.loads((ROOT / "index" / "items" / ITEM_3502 / "transcript.json").read_text(encoding="utf-8"))
    sub = cur["substitui"]
    man = sub["correcoes_manuais_pos_replace"]
    words = [dict(w) for w in cur["words"]]
    for e in sub["edicoes_reaplicadas"] + man["edicoes"]:
        if 831.0 <= words[e["i"]]["start"] <= 836.5 and e in man["edicoes"]:
            continue  # letras soletradas: cru = texto atual
        words[e["i"]]["word"] = e["whisper"]
    f = man["fundidas"][0]
    rec = next(e for e in man["edicoes"] if e["i"] == f["i"])
    w = words[f["i"]]
    second = {"word": f["de"][1], "start": rec["end"], "end": w["end"]}
    w.update(word=f["de"][0], end=rec["end"])
    words.insert(f["i"] + 1, second)
    # conferência: fora da fusão (319–322) e do deslocamento manual 'trazer fazer' -> 'te trazer' (739–740), o cru
    # reconstruído é idêntico ao alinhamento com os segmentos
    seg_raw = m.raw_from_segments(cur["words"], cur["segments"])
    flat = [x for k, x in enumerate(words) if k != f["i"] + 1]
    diffs = [k for k in range(len(flat)) if seg_raw[k] != flat[k]["word"] and not (319 <= k <= 321 or 739 <= k <= 740)]
    assert not diffs, [(k, flat[k]["word"], seg_raw[k]) for k in diffs]
    novo = [dict(_slim(x), raw=x["word"]) for x in words]
    fx = {"item": ITEM_3502, "gerado_em": "2026-10-24", "nota": build_3502_fixture.__doc__.strip(),
          "v1": {"words": [_slim(x) for x in v1["words"]],
                 "segments": [{k: s[k] for k in ("start", "end", "text")} for s in v1["segments"]]},
          "novo_bruto": novo}
    FIX_3502.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(FIX_3502, "wt", encoding="utf-8") as fh:
        json.dump(fx, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"fixture gravada: {FIX_3502} ({FIX_3502.stat().st_size // 1024} KB; v1 {len(v1['words'])} palavras, "
          f"novo {len(novo)})")


def test_carry_3502():
    m = load_t03()
    with gzip.open(FIX_3502, "rt", encoding="utf-8") as fh:
        fx = json.load(fh)
    old, new = fx["v1"]["words"], copy.deepcopy(fx["novo_bruto"])
    c = m.carry_word_edits(new, old, fx["v1"]["segments"])
    print("3502:", {k: (v if not isinstance(v, list) else len(v)) for k, v in c.items()})
    for r in c["nao_reaplicadas"]:
        print("   não reaplicada:", r)
    # 1) toda correção casável voltou, com o mesmo texto que o Gabriel/Claude escolheram à mão
    for io, jn, wd in EXPECT_3502 + ALREADY_OK_3502:
        assert new[jn]["word"] == wd, (io, jn, new[jn]["word"], wd)
    got = {(r["i_antigo"], r["i"]) for r in c["reaplicadas"]}
    assert got == {(io, jn) for io, jn, _ in EXPECT_3502}, sorted(got ^ {(io, jn) for io, jn, _ in EXPECT_3502})
    assert {(r["i_antigo"], r["i"]) for r in c["ja_corretas"]} == {(io, jn) for io, jn, _ in ALREADY_OK_3502}
    # 2) nenhuma diferença entre execuções virou correção: só as palavras acima mudaram
    changed = {j for j, w in enumerate(new) if w["word"] != w["raw"]}
    assert changed == {jn for _, jn, _ in EXPECT_3502}, sorted(changed ^ {jn for _, jn, _ in EXPECT_3502})
    # 3) as 28 correções do v1 são achadas só pelo texto dos segmentos (conferido contra o transcript.v1.txt cru);
    #    as que dependem de fusão ficam listadas para revisão ('D' '&P' -> 'T' 'P' = uma entrada, 2 palavras)
    assert c["fonte"] == "segments" and c["detectadas"] == 28, c["detectadas"]
    assert c["contagem"] == {"detectadas": 28, "reaplicadas": 23, "ja_corretas": 2, "nao_reaplicadas": 3}, c["contagem"]
    nr = {r["i_antigo"]: r for r in c["nao_reaplicadas"]}
    assert set(nr) == {240, 1149}, c["nao_reaplicadas"]
    assert nr[1149]["motivo"] == "estrutural" and nr[1149]["palavras_antigas"] == 2
    assert all(r["trecho_novo"]["texto"] for r in c["nao_reaplicadas"])
    nineteen = [x for x in EXPECT_3502 + ALREADY_OK_3502 if x[0] not in OLD_RULE_3502]
    print(f"3502: {len(nineteen)} das 19 correções refeitas à mão casaram sozinhas "
          f"({len([x for x in nineteen if x in ALREADY_OK_3502])} já vinham certas); "
          f"{len(STRUCTURAL_3502)} eram fusões manuais -> nao_reaplicadas; "
          f"{len(c['reaplicadas'])} reaplicadas no total, 0 espúrias (a regra antiga: 9 reais + 30 espúrias)")
    assert len(nineteen) == 16 and len(nineteen) + len(STRUCTURAL_3502) == 19
    print("regressão IMG_3502 OK")


def _w(word, start, end, raw=None, **kw):
    d = {"word": word, "start": start, "end": end, **kw}
    if raw is not None:
        d["raw"] = raw
    return d


def _closes(c):
    ct = c["contagem"]
    return ct["detectadas"] == ct["reaplicadas"] + ct["ja_corretas"] + ct["nao_reaplicadas"] == c["detectadas"]


def test_carry_rules():
    """Regras finas da reaplicação (unidade, sem subprocesso)."""
    m = load_t03()
    # divisão manual: 1 token do whisper ('polipós-dramatós') -> 2 palavras corrigidas, transcript SEM raw.
    # (a) a execução nova tem 1 palavra só: as duas correções disputam a mesma palavra -> estrutural, nada aplicado
    old = [_w("a", 1.0, 1.2), _w("polipose", 1.2, 1.6), _w("adenomatosa", 1.6, 2.2), _w("familiar", 2.2, 2.6)]
    segs = [{"start": 1.0, "end": 2.6, "text": "a polipós-dramatós familiar"}]
    new = [_w("a", 1.0, 1.2, "a"), _w("polipós-dramatós", 1.22, 2.2, "polipós-dramatós"), _w("familiar", 2.22, 2.6, "familiar")]
    c = m.carry_word_edits(new, old, segs)
    assert [w["word"] for w in new] == ["a", "polipós-dramatós", "familiar"], new
    assert [(r["i_antigo"], r["motivo"], r.get("palavras_antigas")) for r in c["nao_reaplicadas"]] == [(1, "estrutural", 2)], c
    assert _closes(c) and c["contagem"]["nao_reaplicadas"] == 2, c["contagem"]
    # (b) bloco repartido é tudo ou nada: só a 1ª metade casaria -> as duas vão para revisão
    new = [_w("a", 1.0, 1.2, "a"), _w("polipós", 1.22, 1.6, "polipós"), _w("xyzzy", 1.62, 2.2, "xyzzy"),
           _w("familiar", 2.22, 2.6, "familiar")]
    c = m.carry_word_edits(new, old, segs)
    assert [w["word"] for w in new] == ["a", "polipós", "xyzzy", "familiar"] and c["contagem"]["nao_reaplicadas"] == 2, c
    # (c) mesmo bloco com a execução nova também em 2 palavras (caso real do IMG_3502): aplica as duas
    new = [_w("a", 1.0, 1.2, "a"), _w("polipós", 1.22, 1.6, "polipós"), _w("dramatose", 1.62, 2.2, "dramatose"),
           _w("familiar", 2.22, 2.6, "familiar")]
    c = m.carry_word_edits(new, old, segs)
    assert [w["word"] for w in new] == ["a", "polipose", "adenomatosa", "familiar"] and not c["nao_reaplicadas"], c
    # (d) com raw: divisão copiando o mesmo raw nas 2 partes -> estrutural; whisper antigo em 2 palavras corrigidas
    #     uma a uma e o novo em 1 só -> disputa -> estrutural
    old_r = [_w("polipose", 1.2, 1.6, "polipós-dramatós"), _w("adenomatosa", 1.6, 2.2, "polipós-dramatós")]
    new = [_w("polipós-dramatós", 1.22, 2.2, "polipós-dramatós")]
    c = m.carry_word_edits(new, old_r, [])
    assert new[0]["word"] == "polipós-dramatós" and c["nao_reaplicadas"][0]["motivo"] == "estrutural", c
    old_r = [_w("polipose", 1.2, 1.6, "polipós"), _w("adenomatosa", 1.6, 2.2, "-dramatós")]
    new = [_w("polipós-dramatós", 1.22, 2.2, "polipós-dramatós")]
    c = m.carry_word_edits(new, old_r, [])
    assert new[0]["word"] == "polipós-dramatós" and c["contagem"]["nao_reaplicadas"] == 2 and _closes(c), c

    # emenda de janelas (600 s) em transcript sem raw: 'Como' x segmento 'como' é da outra janela -> não é correção;
    # correção de verdade na emenda ('vai' -> 'foi') e só de caixa longe dela ('gist' -> 'GIST') contam
    old = [_w("do", 299.6, 299.9), _w("GIST", 300.0, 300.5), _w("pra", 597.0, 597.2), _w("que", 597.2, 597.4),
           _w("isso", 597.4, 597.7), _w("serve?", 597.7, 598.0), _w("Como", 599.5, 599.8), _w("que", 599.84, 600.08),
           _w("a", 600.06, 600.3), _w("fisiopatologia", 600.3, 601.0), _w("foi", 601.0, 601.3)]
    segs = [{"start": 299.5, "end": 300.6, "text": "do gist"},
            {"start": 597.0, "end": 600.2, "text": "pra que isso serve?"},
            {"start": 599.4, "end": 601.4, "text": "como que a fisiopatologia vai"}]
    new = [dict(w, start=w["start"] + 0.02, end=w["end"] + 0.02) for w in old]
    for w, r in zip(new, ["do", "gist", "pra", "que", "isso", "serve?", "como", "que", "a", "fisiopatologia", "vai"]):
        w["word"] = w["raw"] = r
    c = m.carry_word_edits(new, old, segs)
    assert c["ignoradas_emenda"] == 1 and c["detectadas"] == 2, c
    assert [new[1]["word"], new[6]["word"], new[10]["word"]] == ["GIST", "como", "foi"], new

    # candidato achado só pelos tempos BRUTOS (início refinado muito depois do bruto)
    new = [_w("x", 8.0, 8.3, "x"), _w("tumor", 9.0, 9.3, "tumor"), _w("y", 9.4, 9.7, "y")]
    old = [_w("tumores", 9.6, 9.9, "tumor", raw_start=9.05, raw_end=9.9)]
    c = m.carry_word_edits(new, old, [])
    assert new[1]["word"] == "tumores" and len(c["reaplicadas"]) == 1, c

    # palavra de até 2 letras: tolerância de 70 ms ('e' -> 'é' não cai na 'e' vizinha se a original sumiu)
    old = [_w("é", 5.0, 5.1, "e")]
    new = [_w("e", 5.12, 5.2, "e")]
    c = m.carry_word_edits(new, old, [])
    assert new[0]["word"] == "e" and c["nao_reaplicadas"][0]["motivo"] == "sem_casamento", c
    new = [_w("e", 5.03, 5.12, "e")]
    c = m.carry_word_edits(new, old, [])
    assert new[0]["word"] == "é" and _closes(c), c

    # contagens: sem raw e sem segmentos -> fonte "nenhuma", aviso, nada listado; palavra sem bruto igual na nova
    # conta em sem_bruto.iguais_na_nova (não em ja_corretas)
    c = m.carry_word_edits([_w("a", 1.0, 1.2, "a")], [_w("a", 1.0, 1.2), _w("b", 1.2, 1.4)], [])
    assert c["fonte"] == "nenhuma" and "aviso" in c and not c["nao_reaplicadas"] and c["sem_bruto"]["palavras"] == 2, c
    old = [_w("isso", 1.0, 1.2, "isso"), _w("te", 1.2, 1.4), _w("trazer", 1.4, 1.8, "fazer")]
    new = [_w("isso", 1.0, 1.2, "isso"), _w("te", 1.21, 1.4, "te"), _w("fazer", 1.41, 1.8, "fazer")]
    c = m.carry_word_edits(new, old, [])
    assert c["sem_bruto"] == {"palavras": 1, "iguais_na_nova": 1, "para_revisar": 0} and not c["ja_corretas"], c
    assert new[2]["word"] == "trazer" and _closes(c), c
    print("regras da reaplicação (divisão, emenda, tempos brutos, palavra curta, contagens) OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--real-whisper", action="store_true")
    ap.add_argument("--rebuild-3502", action="store_true", help="recria tests/fixtures/img3502_replace.json.gz e sai")
    a = ap.parse_args()
    if a.rebuild_3502:
        build_3502_fixture()
        return
    test_carry_rules()
    test_carry_3502()
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
        # o import grava "raw" a partir dos segmentos; --backfill-raw refaz isso num transcript antigo sem raw
        imp = json.loads((wdir / "transcript.json").read_text())
        assert all(w.get("raw") == w["word"] for w in imp["words"]), imp["words"][:3]
        for w in imp["words"]:
            del w["raw"]
        imp["words"][2]["word"] = "CRICO"  # correção humana anterior ao raw
        (wdir / "transcript.json").write_text(json.dumps(imp, ensure_ascii=False, separators=(",", ":")))
        sh(PY, S / "03_transcribe.py", iid, "--backfill-raw", env=env)
        bf = json.loads((wdir / "transcript.json").read_text())
        assert [w["raw"] for w in bf["words"]] == [w["word"] for w in stub_transcript(tmp / "_s.json", vid)["words"]]
        assert bf["words"][2]["word"] == "CRICO" and bf["raw_backfill"]["correcoes"] == 1, bf["raw_backfill"]
        bf["words"][2]["word"] = bf["words"][2]["raw"]
        (wdir / "transcript.json").write_text(json.dumps(bf, ensure_ascii=False, separators=(",", ":")))
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
