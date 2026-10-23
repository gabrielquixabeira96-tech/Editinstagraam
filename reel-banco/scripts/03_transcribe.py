#!/usr/bin/env python3
"""03 — Transcrição local de um item de vídeo com faster-whisper (palavra a palavra), no MESMO formato da
reel-editor (`scripts/01_transcribe.py`), para que um trecho do banco possa ir direto para uma EDL.

Saídas:
  work/banco/<item_id>/transcript.json  {"source", "language", "model", "duration", "generated_by",
                                         "refined_boundaries", "words": [{word,start,end,prob[,raw_start,raw_end]}],
                                         "segments": [{start,end,text,avg_logprob,no_speech_prob}]}
  work/banco/<item_id>/transcript.txt   leitura humana: frases com [mm:ss–mm:ss] e #índice da palavra,
                                         marca "── [mm:ss] ──" a cada ~20 s, [pausa X s], <muleta>, (?) = prob < 0,5
  (cópia de transcript.json vai para index/items/<id>/ no --finish do run_ingest)

REGRA: NUNCA retranscreve se transcript.json já existe (no work/ ou no index/). Para corrigir um termo,
edite só o campo "word" — nunca os tempos. Exceção autorizada: transcrição DEFEITUOSA (bug do script, fala
perdida) -> `--replace`, que guarda o transcript antigo como work/banco/<id>/transcript.vN.json (+ .txt),
transcreve de novo e reaplica as correções manuais de "word" (palavras com start/end idênticos ao ms e texto
diferente do whisper; desligue com --no-carry-edits).

Descarte de "não-fala" (conservador — na dúvida, a palavra fica):
  * segmento com no_speech_prob ≥ --no-speech (0,6) SÓ perde as palavras se a energia do trecho estiver no nível
    de silêncio/ruído de fundo: p90 do RMS (quadros de 10 ms) do segmento < piso do arquivo (p10 de todos os
    quadros) + --silence-margin dB (6), ou < --abs-silence dBFS (-70). Com energia de fala, fica (whisper
    marca no_speech alto em fala real com eco, música de fundo, voz baixa ou longe do microfone);
  * palavras que repetem LITERALMENTE o prompt de vocabulário (≥ 3 termos seguidos, na ordem do prompt) saem
    sempre (vazamento do initial_prompt).
  transcript.json → "descartes": {regra, piso_db, limiar_db, total, por_motivo, mantidas_no_speech_alto,
  regra_antiga_descartaria, detalhe[]} — quantas palavras saíram e por quê.

Contorno obrigatório: faster-whisper 1.2.1 + PyAV 19 quebram com caminho de arquivo; o áudio é decodificado
com ffmpeg para numpy float32 mono 16 kHz e o ARRAY é passado a model.transcribe(...).

Memória: o arquivo é transcrito em janelas de --window s (padrão 10 min) lidas do ffmpeg uma a uma (-ss/-t),
com --overlap s (1,5) de sobreposição; tempos deslocados para a timeline do arquivo, cada palavra fica com a
janela que contém o seu meio (sem duplicar na emenda) e o refino RMS é feito por janela. Pico medido ≈ uma
janela (< 2,5 GB para 1 h de áudio). VAD ligado por padrão; o prompt inicial é só uma lista de vocabulário
(frases vazavam como fala). Antes das janelas, um passe só de áudio (-vn, sem decodificar vídeo) mede o RMS
de 10 ms do arquivo inteiro (~400 KB por hora) para o piso de ruído.

Uso:
  python scripts/03_transcribe.py <item_id> [--model small] [--lang pt]
  python scripts/03_transcribe.py <item_id> --replace      (transcrição defeituosa: backup + retranscrição)
  python scripts/03_transcribe.py <item_id> --import-transcript work/<video>/transcript.json
       (reaproveita uma transcrição já feita pela reel-editor/reel-director do MESMO arquivo — não retranscreve)
  REEL_WHISPER_MODEL=medium python scripts/03_transcribe.py <item_id>
"""
import argparse
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402

DEFAULT_PROMPT = ("cricotireoidostomia, intubação, via aérea, plantão, residência, ENARE, cirurgia, anatomia, "
                  "fisiopatologia, conduta, diagnóstico")
FILLERS = {"é", "éé", "ééé", "eh", "ehh", "hã", "ham", "hum", "hmm", "ã", "ãã", "ah", "ahn", "uh", "um", "tipo", "né"}
MARK_EVERY_S = 20.0


def write_txt(tr, path, pause_s=0.6, low_prob=0.5, mark_every=MARK_EVERY_S):
    words = tr["words"]
    lines = [f"# Transcrição de {Path(tr.get('source', '?')).name} — modelo {tr.get('model')} — idioma {tr.get('language')}",
             f"# Duração {bc.fmt_t(tr.get('duration') or 0)} | {len(words)} palavras | "
             f"(?) = confiança < {low_prob} | [pausa X s] = silêncio > {pause_s} s | marca a cada ~{mark_every:.0f} s",
             "# Índices #n referem-se a transcript.json → words[n]", ""]
    phrase, p_start, idx0 = [], None, 0
    prev_end = None
    next_mark = 0.0

    def flush(end_t):
        nonlocal phrase, p_start
        if phrase:
            lines.append(f"[{bc.fmt_t(p_start)}–{bc.fmt_t(end_t)}] #{idx0:<5} " + " ".join(phrase))
        phrase, p_start = [], None

    for i, w in enumerate(words):
        if prev_end is not None and w["start"] - prev_end > pause_s:
            flush(prev_end)
            lines.append(f"            [pausa {w['start'] - prev_end:.2f} s]")
        if p_start is None:
            if w["start"] >= next_mark:
                lines.append(f"── [{bc.fmt_t(w['start'])}] ──")
                next_mark = (int(w["start"] // mark_every) + 1) * mark_every
            p_start, idx0 = w["start"], i
        tok = w["word"].strip()
        if w.get("prob", 1) < low_prob:
            tok += "(?)"
        if re.sub(r"[^\w]", "", tok.replace("(?)", "").lower()) in FILLERS:  # mantém acento: "é" ≠ "e", "ã" ≠ "a"
            tok = f"<{tok}>"
        phrase.append(tok)
        prev_end = w["end"]
        if tok.rstrip("(?)>").endswith((".", "?", "!")) or len(phrase) >= 14:
            flush(w["end"])
    if prev_end is not None:
        flush(prev_end)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def refine_boundaries(words, audio, sr=16000, hop_s=0.01, min_dur=0.06):
    """Idêntico à reel-editor: apara start/end para onde há energia de voz (RMS 10 ms). Nunca alarga;
    guarda os tempos originais em raw_start/raw_end."""
    import numpy as np
    hop = int(hop_s * sr)
    nfr = len(audio) // hop
    if nfr < 10:
        return words
    rms = np.sqrt(np.mean(audio[:nfr * hop].reshape(nfr, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    floor = np.percentile(db, 10)
    speech = np.percentile(db, 95)
    thr = max(floor + 0.35 * (speech - floor), speech - 35)
    voiced = db > thr
    for w in words:
        a, b = int(w["start"] / hop_s), int(np.ceil(w["end"] / hop_s))
        a, b = max(0, a), min(nfr, b)
        if b - a < 2:
            continue
        idx = np.where(voiced[a:b])[0]
        if not len(idx):
            continue
        clusters, c0, prev = [], idx[0], idx[0]
        for k in idx[1:]:
            if k - prev > 20:
                clusters.append((c0, prev))
                c0 = k
            prev = k
        clusters.append((c0, prev))
        c0, c1 = max(clusters, key=lambda c: c[1] - c[0])
        ns = (a + c0) * hop_s - 0.02
        ne = (a + c1 + 1) * hop_s + 0.04
        ns, ne = max(w["start"], ns), min(w["end"], ne)
        if ne - ns >= min_dur and (ns > w["start"] + 0.03 or ne < w["end"] - 0.03):
            w["raw_start"], w["raw_end"] = w["start"], w["end"]
            w["start"], w["end"] = round(ns, 3), round(ne, 3)
    return words


HOP_S = 0.01  # quadros de 10 ms para o RMS de descarte


def file_energy_db(src, sr=16000, hop_s=HOP_S, chunk_s=60.0):
    """RMS em dB de quadros de 10 ms do arquivo INTEIRO, num único ffmpeg só de áudio (-vn: nunca decodifica
    vídeo, nem em 4K) lido em blocos de 60 s — memória ~400 KB por hora de áudio."""
    import subprocess
    import numpy as np
    hop = int(hop_s * sr)
    cmd = [bc.ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(src), "-vn", "-f", "s16le", "-ac", "1",
           "-ar", str(sr), "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out, rest = [], np.zeros(0, np.float32)
    nbytes = int(chunk_s * sr) * 2
    while True:
        buf = p.stdout.read(nbytes)
        if not buf:
            break
        x = np.concatenate([rest, np.frombuffer(buf[:len(buf) // 2 * 2], np.int16).astype(np.float32) / 32768.0])
        n = len(x) // hop
        if n:
            fr = x[:n * hop].reshape(n, hop)
            out.append(20 * np.log10(np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)))
        rest = x[n * hop:]
    p.wait()
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def span_p90_db(frame_db, t0, t1, hop_s=HOP_S):
    import numpy as np
    a, b = max(0, int(t0 / hop_s)), min(len(frame_db), int(math.ceil(t1 / hop_s)))
    if b - a < 1:
        return None
    return float(np.percentile(frame_db[a:b], 90))


def prompt_terms(prompt):
    """[(token_normalizado, índice_do_termo)] na ordem do prompt de vocabulário."""
    seq = []
    for k, term in enumerate(t for t in (prompt or "").split(",") if t.strip()):
        for tok in term.split():
            n = bc.norm_word(tok)
            if n:
                seq.append((n, k))
    return seq


def prompt_leak_mask(norm_words, pseq, min_terms=3):
    """True nas palavras que fazem parte de uma repetição LITERAL de ≥ min_terms termos seguidos do prompt."""
    mask = [False] * len(norm_words)
    if not pseq:
        return mask
    ptoks = [t for t, _ in pseq]
    i = 0
    while i < len(norm_words):
        best = 0
        for p in (j for j, t in enumerate(ptoks) if t == norm_words[i]):
            k = 0
            while i + k < len(norm_words) and p + k < len(ptoks) and norm_words[i + k] == ptoks[p + k]:
                k += 1
            if len({pseq[p + m][1] for m in range(k)}) >= min_terms:
                best = max(best, k)
        if best:
            for m in range(best):
                mask[i + m] = True
            i += best
        else:
            i += 1
    return mask


def transcribe_windows(model, src, dur, args, frame_db=None):
    """Transcreve em janelas de --window s lidas do ffmpeg uma a uma (-ss/-t), com --overlap s de sobreposição
    de cada lado. Memória limitada a uma janela (o faster-whisper calcula o espectrograma da entrada inteira:
    o arquivo todo de uma vez custa ~3 GB/hora). Cada janela "possui" o intervalo [k·W, (k+1)·W): palavras e
    segmentos são atribuídos pela metade do intervalo -> a emenda não duplica nem perde palavras. O refino de
    limites por energia é feito com o áudio da própria janela.

    Descarte (ver docstring do módulo): no_speech alto só descarta se o trecho for silêncio/ruído de fundo pela
    energia (piso do arquivo + margem); repetição literal do prompt sai sempre. Devolve (words, segments, desc)."""
    import numpy as np
    if frame_db is None:
        frame_db = file_energy_db(src)
    floor = float(np.percentile(frame_db, 10)) if len(frame_db) else -120.0
    thr = max(floor + float(args.silence_margin), float(args.abs_silence))
    pseq = prompt_terms(args.prompt)
    W, O = float(args.window), float(args.overlap)
    nwin = max(1, math.ceil(dur / W))
    words, segments = [], []
    desc = {"regra": (f"no_speech_prob >= {args.no_speech} E p90 RMS do segmento < max(piso + "
                      f"{args.silence_margin} dB, {args.abs_silence} dBFS); ou repeticao literal do prompt "
                      f"(>= 3 termos)"),
            "no_speech_limiar": args.no_speech, "piso_db": round(floor, 1), "limiar_db": round(thr, 1),
            "total": 0, "por_motivo": {"silencio": 0, "prompt": 0}, "mantidas_no_speech_alto": 0,
            "regra_antiga_descartaria": 0, "detalhe": []}
    for k in range(nwin):
        own0, own1 = k * W, min(dur, (k + 1) * W)
        if k == nwin - 1:
            own1 = dur + 10.0  # tudo o que sobrar no fim
        a = max(0.0, own0 - O)
        b = min(dur, (k + 1) * W + O)
        tw = time.time()
        audio = bc.decode_audio_mono(src, 16000, start=a, dur=b - a)
        if len(audio) < 1600:
            continue
        segs, _info = model.transcribe(audio, language=args.lang, word_timestamps=True, beam_size=args.beam_size,
                                       vad_filter=not args.no_vad, initial_prompt=args.prompt or None,
                                       condition_on_previous_text=False)
        wwords = []
        for s in segs:
            mid = a + (s.start + s.end) / 2
            sw = [w for w in (s.words or []) if w.end > w.start and own0 <= a + (w.start + w.end) / 2 < own1]
            high_ns = s.no_speech_prob >= args.no_speech
            p90 = span_p90_db(frame_db, a + s.start, a + s.end) if high_ns else None
            silent = high_ns and (p90 is None or p90 < thr)
            leak = prompt_leak_mask([bc.norm_word(w.word) for w in sw], pseq)
            n_sil = n_leak = n_kept_ns = 0
            for w, lk in zip(sw, leak):
                if high_ns:
                    desc["regra_antiga_descartaria"] += 1
                if silent:
                    n_sil += 1
                    continue
                if lk:
                    n_leak += 1
                    continue
                if high_ns:
                    n_kept_ns += 1
                wwords.append({"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
                               "prob": round(w.probability, 3)})
            desc["por_motivo"]["silencio"] += n_sil
            desc["por_motivo"]["prompt"] += n_leak
            desc["mantidas_no_speech_alto"] += n_kept_ns
            if n_sil or n_leak:
                desc["detalhe"].append({"start": round(a + s.start, 2), "end": round(a + s.end, 2),
                                        "motivo": "silencio" if n_sil else "prompt", "palavras": n_sil + n_leak,
                                        "no_speech_prob": round(s.no_speech_prob, 3),
                                        "p90_db": None if p90 is None else round(p90, 1),
                                        "texto": s.text.strip()[:160]})
            if own0 <= mid < own1 and not silent and not (sw and all(leak)):
                seg = {"start": round(a + s.start, 3), "end": round(a + s.end, 3), "text": s.text.strip(),
                       "avg_logprob": round(s.avg_logprob, 3), "no_speech_prob": round(s.no_speech_prob, 3)}
                if high_ns:
                    seg["p90_db"] = round(p90, 1)
                    seg["no_speech_mantido"] = True
                segments.append(seg)
        wwords.sort(key=lambda w: w["start"])
        if not args.no_refine:
            refine_boundaries(wwords, audio)  # tempos ainda relativos à janela
        for w in wwords:
            for kk in ("start", "end", "raw_start", "raw_end"):
                if kk in w:
                    w[kk] = round(w[kk] + a, 3)
        # desduplicação defensiva na emenda (mesma palavra, início a < 0,15 s da anterior)
        for w in wwords:
            if words and bc.norm_word(words[-1]["word"]) == bc.norm_word(w["word"]) and abs(words[-1]["start"] - w["start"]) < 0.15:
                continue
            words.append(w)
        del audio
        bc.log(f"  janela {k + 1}/{nwin} [{bc.fmt_t(own0)}–{bc.fmt_t(min(dur, own1))}]: {len(wwords)} palavras "
               f"({time.time() - tw:.0f} s)")
    words.sort(key=lambda w: w["start"])
    segments.sort(key=lambda s: s["start"])
    desc["total"] = desc["por_motivo"]["silencio"] + desc["por_motivo"]["prompt"]
    return words, segments, desc


def carry_word_edits(new_words, old_words):
    """Reaplica correções manuais do campo "word": mesma palavra (start/end idênticos ao ms) com texto diferente.
    O whisper é determinístico para o mesmo áudio/modelo/parâmetros, então texto diferente no MESMO tempo é
    edição humana. Devolve a lista [(i, antes, depois)]."""
    old = {(round(w["start"], 3), round(w["end"], 3)): w["word"] for w in old_words}
    out = []
    for i, w in enumerate(new_words):
        ow = old.get((round(w["start"], 3), round(w["end"], 3)))
        if ow is not None and ow != w["word"]:
            out.append((i, w["word"], ow))
            w["word"] = ow
    return out


def validate_transcript(tr):
    if not isinstance(tr, dict) or "words" not in tr or "segments" not in tr:
        bc.die("transcript.json inválido: precisa de 'words' e 'segments' (formato reel-editor)")
    for w in tr["words"][:50]:
        for k in ("word", "start", "end"):
            if k not in w:
                bc.die(f"transcript.json inválido: palavra sem '{k}'")


def main():
    ap = argparse.ArgumentParser(description="Transcreve um item de vídeo do banco (formato reel-editor).")
    ap.add_argument("item_id")
    ap.add_argument("--model", default=os.environ.get("REEL_WHISPER_MODEL", "small"),
                    help="tiny/base/small/medium/large-v3 (padrão: small ou REEL_WHISPER_MODEL)")
    ap.add_argument("--lang", default=os.environ.get("REEL_LANG", "pt"))
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--beam-size", type=int, default=5)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help="prompt inicial SÓ de vocabulário (lista de termos; frases vazam como fala)")
    ap.add_argument("--no-vad", action="store_true", help="desliga o vad_filter (ligado por padrão: evita "
                                                           "alucinação em silêncio/música)")
    ap.add_argument("--no-speech", type=float, default=0.6,
                    help="segmentos com no_speech_prob ≥ este valor (padrão 0,6) são CANDIDATOS a descarte: só saem "
                         "se a energia também for de silêncio (ver --silence-margin)")
    ap.add_argument("--silence-margin", type=float, default=6.0,
                    help="dB acima do piso de ruído do arquivo (p10 dos quadros de 10 ms) abaixo dos quais o p90 do "
                         "segmento conta como silêncio (padrão 6; menor = mais conservador)")
    ap.add_argument("--abs-silence", type=float, default=-70.0,
                    help="p90 abaixo deste dBFS é silêncio em qualquer arquivo (padrão -70)")
    ap.add_argument("--window", type=float, default=600.0, help="janela de transcrição em s (padrão 600 = 10 min)")
    ap.add_argument("--overlap", type=float, default=1.5, help="sobreposição entre janelas em s (padrão 1,5)")
    ap.add_argument("--no-refine", action="store_true")
    ap.add_argument("--import-transcript", help="copia um transcript.json existente (do MESMO arquivo) em vez de transcrever")
    ap.add_argument("--force", action="store_true", help="sobrescreve transcript.json existente (só se a MÍDIA mudou)")
    ap.add_argument("--replace", action="store_true",
                    help="transcrição DEFEITUOSA (bug, fala perdida): guarda a atual em transcript.vN.json(+.txt) no "
                         "work/, retranscreve e reaplica as correções de 'word' (ver --no-carry-edits)")
    ap.add_argument("--no-carry-edits", action="store_true",
                    help="com --replace: NÃO reaplica as correções manuais de 'word' do transcript antigo")
    args = ap.parse_args()

    bc.set_log_item(args.item_id)
    item = bc.load_item(args.item_id)
    if item.get("tipo") != "video":
        bc.die(f"{args.item_id} é do tipo {item.get('tipo')} — transcrição só para vídeo/áudio")
    d = bc.item_dir(args.item_id)
    out = d / "transcript.json"
    txt = d / "transcript.txt"
    idx_copy = bc.index_item_dir(args.item_id) / "transcript.json"
    backup, old_tr = None, None
    if args.replace:
        if args.import_transcript:
            bc.die("--replace não combina com --import-transcript")
        if not bc.find_source(args.item_id):
            bc.die(f"--replace: mídia de {args.item_id} ausente em {d} (rode 02_fetch.py --item {args.item_id}); "
                   "nada foi alterado")
        cur = out if out.exists() else (idx_copy if idx_copy.exists() else None)
        if cur:
            d.mkdir(parents=True, exist_ok=True)
            n = 1
            while (d / f"transcript.v{n}.json").exists():
                n += 1
            backup = d / f"transcript.v{n}.json"
            old_tr = bc.load_json(cur)
            shutil.copy2(cur, backup)
            if txt.exists():
                shutil.copy2(txt, d / f"transcript.v{n}.txt")
            bc.log(f"{args.item_id}: --replace — transcript atual ({len(old_tr.get('words', []))} palavras) "
                   f"guardado em {backup.name}; retranscrevendo")
        args.force = True
    if not out.exists() and idx_copy.exists() and not args.force:
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(idx_copy, out)
        bc.log(f"{args.item_id}: transcript.json recuperado do índice (não retranscrevo)")
    if out.exists() and not args.force:
        bc.log(f"{args.item_id}: transcript.json já existe — não vou transcrever de novo")
        tr = bc.load_json(out)
        if not txt.exists():
            write_txt(tr, txt)
        return

    if args.import_transcript:
        tr = bc.load_json(args.import_transcript)
        validate_transcript(tr)
        tr.setdefault("imported_from", str(Path(args.import_transcript).resolve()))
        dur = item.get("duracao")
        if dur and tr.get("duration") and abs(float(tr["duration"]) - float(dur)) > 1.0:
            bc.warn(f"duração do transcript ({tr['duration']:.1f} s) difere da mídia ({dur:.1f} s) — confira se é o mesmo arquivo")
        bc.save_json(out, tr, compact=True)
        write_txt(tr, txt)
        bc.log(f"{args.item_id}: transcript importado ({len(tr['words'])} palavras) -> {out}")
        return

    src = bc.find_source(args.item_id)
    if not src:
        bc.die(f"mídia de {args.item_id} ausente em {d} (rode 02_fetch.py)")
    if not item.get("tem_audio", True):
        bc.die("a mídia não tem trilha de áudio")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        bc.die("faster-whisper não instalado: pip install --break-system-packages faster-whisper")

    dur = float(item.get("duracao") or bc.probe(src).get("duration") or 0)
    if dur <= 0:
        bc.die("duração da mídia desconhecida (ffprobe falhou)")
    bc.log(f"carregando modelo '{args.model}' ...")
    try:
        model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    except Exception as e:
        bc.die(f"não foi possível carregar o modelo '{args.model}': {e}\n"
               "Sem internet? Importe um transcript.json pronto com --import-transcript.")
    nwin = max(1, math.ceil(dur / args.window))
    bc.log(f"transcrevendo o arquivo INTEIRO ({bc.fmt_t(dur)}) em {nwin} janela(s) de {args.window:.0f} s "
           f"(sobreposição {args.overlap} s; CPU 'small': ~0,3–1x o tempo real) ...")
    t_start = time.time()
    words, segments, desc = transcribe_windows(model, src, dur, args)
    tr = {"source": str(src), "language": args.lang, "model": args.model, "duration": dur,
          "generated_by": "reel-banco/03_transcribe.py", "refined_boundaries": not args.no_refine,
          "descartes": desc, "words": words, "segments": segments}
    t0 = t_start
    bc.log(f"  descarte: {desc['total']} palavra(s) (silêncio {desc['por_motivo']['silencio']}, prompt "
           f"{desc['por_motivo']['prompt']}); {desc['mantidas_no_speech_alto']} mantida(s) com no_speech alto e "
           f"energia de fala (a regra antiga descartaria {desc['regra_antiga_descartaria']}); piso "
           f"{desc['piso_db']} dB, limiar {desc['limiar_db']} dB")
    if old_tr is not None:
        edits = [] if args.no_carry_edits else carry_word_edits(words, old_tr.get("words", []))
        tr["substitui"] = {"backup": backup.name, "palavras_antes": len(old_tr.get("words", [])),
                           "palavras_depois": len(words), "em": bc.now_iso(),
                           "edicoes_reaplicadas": [{"i": i, "whisper": a_, "word": b_} for i, a_, b_ in edits]}
        bc.log(f"  --replace: {len(old_tr.get('words', []))} -> {len(words)} palavras; {len(edits)} correção(ões) "
               f"de 'word' reaplicada(s)" + ("" if args.no_carry_edits else " (start/end idênticos)"))
    bc.save_json(out, tr, compact=True)
    write_txt(tr, txt)
    low = sum(1 for w in words if w["prob"] < 0.5)
    bc.log(f"{args.item_id}: OK {len(words)} palavras ({low} com confiança < 0,5) em {time.time() - t0:.0f} s -> {out}")


if __name__ == "__main__":
    main()
