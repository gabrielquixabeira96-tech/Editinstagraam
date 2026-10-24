#!/usr/bin/env python3
"""03 — Transcrição local de um item de vídeo com faster-whisper (palavra a palavra), no MESMO formato da
reel-editor (`scripts/01_transcribe.py`), para que um trecho do banco possa ir direto para uma EDL.

Saídas:
  work/banco/<item_id>/transcript.json  {"source", "language", "model", "duration", "generated_by",
                                         "refined_boundaries", "janelas": {window, overlap},
                                         "words": [{word,start,end,prob,raw[,raw_start,raw_end]}],
                                         "segments": [{start,end,text,avg_logprob,no_speech_prob}]}
                                         "raw" = texto que o whisper escreveu (desde 2026-10-24; NUNCA editar)
  work/banco/<item_id>/transcript.txt   leitura humana: frases com [mm:ss–mm:ss] e #índice da palavra,
                                         marca "── [mm:ss] ──" a cada ~20 s, [pausa X s], <muleta>, (?) = prob < 0,5
  (cópia de transcript.json vai para index/items/<id>/ no --finish do run_ingest)

REGRA: NUNCA retranscreve se transcript.json já existe (no work/ ou no index/). Para corrigir um termo,
edite só o campo "word" — nunca os tempos, o "raw" nem o texto dos segmentos. Exceção autorizada: transcrição
DEFEITUOSA (bug do script, fala perdida) -> `--replace`, que guarda o transcript antigo como
work/banco/<id>/transcript.vN.json (+ .txt), transcreve de novo e reaplica as correções manuais de "word"
(desligue com --no-carry-edits). Correção manual = "word" diferente do texto bruto do whisper ("raw" da
palavra; em transcript sem "raw", o texto de "segments", alinhado às palavras). A reaplicação é tolerante,
porque o whisper NÃO sai bit-idêntico entre execuções (tempos deslocam ~20 ms, pontuação/caixa e algumas
palavras mudam): cada correção casa com a palavra nova a ≤ 150 ms (início ou fim; ≤ 70 ms se tiver até 2
letras) cujo texto bruto se parece com o bruto antigo (SequenceMatcher ≥ 0,6 sem acento/pontuação/caixa;
palavra de ≤ 2 letras só igual). Correção que muda o número de palavras (fusão, divisão) é estrutural e não é
aplicada. Sem casamento -> transcript.json → substitui.nao_reaplicadas, impressa no fim para revisão à mão.

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
       (reaproveita uma transcrição já feita pela reel-editor/reel-director do MESMO arquivo — não retranscreve;
        grava "raw" a partir dos segmentos)
  python scripts/03_transcribe.py <item_id> --backfill-raw (grava "raw" num transcript antigo, sem retranscrever)
  REEL_WHISPER_MODEL=medium python scripts/03_transcribe.py <item_id>
"""
import argparse
import bisect
import difflib
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
                               "prob": round(w.probability, 3), "raw": w.word.strip()})
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


# --- reaplicação de correções manuais no --replace -------------------------------------------------------------
CARRY_TOL_S = 0.15     # |Δinício| ou |Δfim| máximo entre a palavra corrigida antiga e a nova
SHORT_TOL_S = 0.07     # idem para palavra de até 2 letras ('e', 'é', 'de'): vizinha curta igual é comum
CARRY_MIN_SIM = 0.6    # similaridade mínima entre o texto bruto antigo e o novo (sem acento/pontuação/caixa)
MAX_UNEVEN_BLOCK = 6   # bloco desigual maior que isso no alinhamento com os segmentos = desalinhado (ignora)
DEFAULT_WINDOW_S, DEFAULT_OVERLAP_S = 600.0, 1.5   # janelas usadas nos transcripts sem o campo "janelas"


def split_punct(s):
    """(pontuação inicial, miolo, pontuação final): '-dramatós,' -> ('-', 'dramatós', ',')."""
    m = re.match(r"^(\W*)(.*?)(\W*)$", s or "", re.S)
    return m.group(1), m.group(2), m.group(3)


def norm_case_punct(s):
    """Sem pontuação e sem caixa, mas COM acento ('pancreatico' ≠ 'pancreático')."""
    return re.sub(r"[^\w]", "", (s or "").lower())


def raw_similarity(a, b):
    """Similaridade 0–1 entre dois textos brutos do whisper, sem acento, pontuação nem caixa. Palavra de até 2
    letras só casa se for igual ('d' não casa com 'de')."""
    na, nb = bc.norm_word(a), bc.norm_word(b)
    if not na or not nb:
        return 1.0 if (a or "").strip() == (b or "").strip() else 0.0
    if na == nb:
        return 1.0
    if min(len(na), len(nb)) <= 2:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def in_seam(w, window, overlap):
    """Palavra a até `overlap` s de uma emenda k·window (k ≥ 1) entre janelas de transcrição."""
    if not window or window <= 0:
        return False
    k = round(((w["start"] + w["end"]) / 2) / window)
    return k >= 1 and w["end"] >= k * window - overlap and w["start"] <= k * window + overlap


def _apportion(rtoks, wtoks):
    """Reparte o texto bruto de um bloco desigual entre as palavras do bloco, por alinhamento de caracteres,
    quando os textos ainda se parecem (o whisper juntou/separou tokens: 'polipós-dramatós' x 'polipose'
    'adenomatosa'). Se não se parecem (trecho reescrito à mão: 'assim, não é indicando,' -> 'síndrome de
    Carney,'; fusão 'é indicando,' -> 'Carney,'), cada palavra do bloco recebe o bruto inteiro, com espaços —
    a reaplicação trata isso como correção estrutural (revisão à mão)."""
    whole = " ".join(rtoks)
    R, C = "".join(rtoks), "".join(wtoks)
    sm = difflib.SequenceMatcher(None, R, C, autojunk=False)
    if len(wtoks) == 1 or sm.ratio() < 0.5:
        return [whole] * len(wtoks)
    ops = sm.get_opcodes()

    def to_r(c):
        for tag, a0, a1, b0, b1 in ops:
            if b0 <= c <= b1:
                return a0 + (c - b0) if tag == "equal" else a0 + round((c - b0) * (a1 - a0) / max(1, b1 - b0))
        return len(R)

    cuts, acc = [0], 0
    for w in wtoks[:-1]:
        acc += len(w)
        cuts.append(max(cuts[-1], to_r(acc)))
    cuts.append(max(cuts[-1], len(R)))
    return [R[cuts[k]:cuts[k + 1]] for k in range(len(wtoks))]


def raw_from_segments(words, segments, split_groups=None):
    """Texto bruto do whisper de cada palavra de um transcript SEM o campo "raw" (anteriores a 2026-10-24): o
    texto dos segmentos nunca é editado (só "word" é), então o alinhamento global das palavras com os tokens dos
    segmentos devolve o que o whisper escreveu — inclusive na emenda das janelas, onde dois segmentos se
    sobrepõem. None = palavra sem correspondente confiável (carry_word_edits a manda para revisão, a não ser que a
    palavra nova no mesmo lugar já tenha o mesmo texto). Se `split_groups` for uma lista, recebe (índices, bruto
    inteiro) de cada bloco desigual repartido por caractere (nº de tokens ≠ nº de palavras, textos parecidos)."""
    segs = sorted((s for s in (segments or []) if s.get("text")), key=lambda s: (s["start"], s["end"]))
    rtoks = [t for s in segs for t in s["text"].split()]
    wtoks = [w["word"] for w in words]
    out = [None] * len(words)
    if not rtoks:
        return out
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(None, rtoks, wtoks, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and a1 - a0 == b1 - b0):
            for k in range(b1 - b0):
                out[b0 + k] = rtoks[a0 + k]
        elif tag == "replace" and max(a1 - a0, b1 - b0) <= MAX_UNEVEN_BLOCK:
            pieces = _apportion(rtoks[a0:a1], wtoks[b0:b1])
            for k, r in enumerate(pieces):
                out[b0 + k] = r
            if split_groups is not None and b1 - b0 > 1 and len(set(pieces)) > 1:
                split_groups.append((list(range(b0, b1)), " ".join(rtoks[a0:a1])))
    return out


def old_raw_texts(old_words, old_segments, seams=None):
    """Texto bruto do whisper por palavra do transcript antigo: "raw" da palavra; sem ele, o alinhamento com os
    segmentos. Devolve dict: raws, fonte ("raw" | "segments" | "raw+segments" | "nenhuma"), de_segmentos
    (índices cujo bruto veio dos segmentos), grupos (blocos repartidos — ver raw_from_segments) e emenda (índices
    vindos dos segmentos em que word e bruto diferem só em pontuação/caixa perto de uma emenda de janelas: lá o
    texto do segmento pode ser o da outra janela, então isso não conta como correção)."""
    window, overlap = seams or (DEFAULT_WINDOW_S, DEFAULT_OVERLAP_S)
    seg_raw, groups, raws, from_seg, fontes = None, [], [], set(), set()
    for i, w in enumerate(old_words):
        r = w.get("raw")
        if r is not None:
            fontes.add("raw")
        else:
            if seg_raw is None:
                seg_raw = raw_from_segments(old_words, old_segments, groups)
            r = seg_raw[i]
            if r is not None:
                fontes.add("segments")
                from_seg.add(i)
        raws.append(r)
    seam = {i for i in from_seg if raws[i] != old_words[i]["word"] and in_seam(old_words[i], window, overlap)
            and norm_case_punct(raws[i]) == norm_case_punct(old_words[i]["word"])}
    groups = [(g, whole) for g, whole in groups if any(i in from_seg for i in g)]
    return {"raws": raws, "fonte": "+".join(sorted(fontes)) or "nenhuma", "de_segmentos": from_seg,
            "grupos": groups, "emenda": seam}


def _time_dist(a, b):
    d = min(abs(a["start"] - b["start"]), abs(a["end"] - b["end"]))
    if "raw_start" in a or "raw_start" in b:
        d = min(d, abs(a.get("raw_start", a["start"]) - b.get("raw_start", b["start"])),
                abs(a.get("raw_end", a["end"]) - b.get("raw_end", b["end"])))
    return d


def transfer_edit(old_raw, old_word, new_raw):
    """Texto final da palavra nova. Se a correção mexeu só no miolo (pontuação igual à do bruto antigo), a
    pontuação da execução nova é preservada ('giste,' -> 'GIST,' sobre 'existe.' dá 'GIST.'); se a correção
    mexeu na pontuação ('h' -> 'H,'), vale a palavra corrigida inteira."""
    ol, _oc, ot = split_punct(old_raw)
    wl, wc, wt = split_punct(old_word)
    nl, _nc, nt = split_punct(new_raw)
    if wc and (wl, wt) == (ol, ot):
        return nl + wc + nt
    return old_word


def carry_word_edits(new_words, old_words, old_segments=None, tol=CARRY_TOL_S, min_sim=CARRY_MIN_SIM,
                     short_tol=SHORT_TOL_S, seams=None):
    """Reaplica, nas palavras novas (recém-saídas do whisper, com "raw"), as correções manuais de "word" do
    transcript antigo. Correção = "word" antigo ≠ texto bruto antigo (ver old_raw_texts); diferenças entre as
    duas execuções do whisper (pontuação, caixa, tempo, palavra trocada) NÃO são correções e não são tocadas.

    Casamento: palavra nova com |Δinício| ou |Δfim| ≤ tol (tempos refinados ou brutos; ≤ short_tol se a palavra
    tiver até 2 letras) e raw_similarity(bruto antigo, bruto novo) ≥ min_sim — ou bruto novo já igual à
    correção —, a melhor primeiro (maior similaridade, depois menor Δt), cada palavra nova usada uma vez.

    Estrutural (não aplica; vai para revisão): a correção mudou o número de palavras — bruto com espaço (fusão),
    o mesmo bruto em palavras corrigidas vizinhas (divisão), duas correções disputando a mesma palavra nova, ou
    bloco repartido dos segmentos que não casou inteiro (tudo ou nada). Palavra antiga sem bruto conhecido (em
    geral uma correção que deslocou palavras, como 'trazer fazer' -> 'te trazer') só é dada como resolvida se a
    nova já tiver o mesmo texto; senão vai para revisão. Sem fonte de bruto (nem "raw" nem segmentos), nada é
    detectado e "aviso" diz isso.

    Altera new_words e devolve {"fonte", "detectadas", "reaplicadas", "ja_corretas", "nao_reaplicadas",
    "contagem", "sem_bruto", "ignoradas_emenda"[, "aviso"]}; contagem fecha: detectadas = reaplicadas +
    ja_corretas + nao_reaplicadas (em palavras; uma entrada de nao_reaplicadas pode cobrir várias)."""
    info = old_raw_texts(old_words, old_segments, seams)
    raws = info["raws"]
    edits = [i for i, w in enumerate(old_words)
             if raws[i] is not None and w["word"] != raws[i] and i not in info["emenda"]]
    eset = set(edits)
    unknown = [i for i, r in enumerate(raws) if r is None and bc.norm_word(old_words[i]["word"])]
    aviso = None
    if unknown and (info["fonte"] == "nenhuma" or len(unknown) > max(20, 0.2 * len(old_words))):
        aviso = (f"{len(unknown)} de {len(old_words)} palavras antigas sem texto bruto conhecido (sem 'raw' e "
                 f"segmentos {'ausentes' if info['fonte'] == 'nenhuma' else 'desalinhados'}): correções nelas não "
                 "são detectáveis — compare à mão com o backup")
        n_unknown_total, unknown = len(unknown), []
    else:
        n_unknown_total = len(unknown)
    structural = {i for i in edits if len(raws[i].split()) > 1
                  or (i - 1 in eset and raws[i - 1] == raws[i]) or (i + 1 in eset and raws[i + 1] == raws[i])}
    nraw = [w.get("raw", w["word"]) for w in new_words]
    order = sorted(range(len(new_words)), key=lambda j: new_words[j]["start"])
    starts = [new_words[j]["start"] for j in order]
    maxdur = min(10.0, max((max(w["end"] - w["start"], w.get("raw_end", w["end"]) - w.get("raw_start", w["start"]))
                            for w in new_words), default=0.0))
    cands = {}
    for i in edits + unknown:
        if i in structural:
            continue
        ow = old_words[i]
        t0 = min(ow["start"], ow.get("raw_start", ow["start"]))
        t1 = max(ow["end"], ow.get("raw_end", ow["end"]))
        lo = bisect.bisect_left(starts, t0 - tol - maxdur)
        hi = bisect.bisect_right(starts, t1 + tol + maxdur)
        for j in order[lo:hi]:
            dt = _time_dist(ow, new_words[j])
            if dt > tol:
                continue
            sim = raw_similarity(raws[i], nraw[j]) if raws[i] is not None else 0.0
            if sim < 1.0 and bc.norm_word(ow["word"]) and bc.norm_word(ow["word"]) == bc.norm_word(nraw[j]):
                sim = 1.0  # o whisper novo já escreveu a forma corrigida
            if sim < min_sim:
                continue
            short = min(len(bc.norm_word(raws[i] if raws[i] is not None else ow["word"])),
                        len(bc.norm_word(nraw[j]))) <= 2
            if short and dt > short_tol:
                continue
            cands.setdefault(i, []).append((-sim, dt, j))
    pairs = sorted((nsim, dt, i, j) for i, cl in cands.items() for nsim, dt, j in cl)
    assigned, owner = {}, {}
    for nsim, dt, i, j in pairs:
        if i in assigned or j in owner:
            continue
        assigned[i] = (j, -nsim, dt)
        owner[j] = i
    # duas correções disputando a mesma palavra nova (a perdedora não tem outra): divisão/fusão -> estrutural
    for i, cl in cands.items():
        if i in assigned or i not in eset:
            continue
        rivals = {owner[j] for _s, _d, j in cl if j in owner and owner[j] in eset}
        if rivals:
            structural |= {i} | rivals
    # bloco repartido dos segmentos: tudo ou nada
    for g, _whole in info["grupos"]:
        ge = [i for i in g if i in eset]
        if ge and any(i in structural or i not in assigned for i in ge):
            structural |= set(ge)
    for i in structural:
        if i in assigned:
            del owner[assigned.pop(i)[0]]

    def trecho(t0, t1):  # palavras novas em volta, para quem for corrigir à mão
        near = [j for j in order[bisect.bisect_left(starts, t0 - 1.0):bisect.bisect_right(starts, t1 + 0.5)]
                if new_words[j]["end"] > t0 - 0.5]
        return {"i": near, "texto": " ".join(nraw[j] for j in near)}

    out = {"fonte": info["fonte"], "detectadas": len(edits), "reaplicadas": [], "ja_corretas": [],
           "nao_reaplicadas": [], "ignoradas_emenda": len(info["emenda"])}
    nr = out["nao_reaplicadas"]
    n_unknown_equal = 0
    last = None  # (índice antigo, motivo) da última entrada, para juntar palavras estruturais vizinhas
    for i in sorted(edits + unknown):
        ow = old_words[i]
        if i not in assigned:
            motivo = "estrutural" if i in structural else ("sem_bruto" if raws[i] is None else "sem_casamento")
            if motivo == "estrutural" and last and last == (i - 1, "estrutural"):
                prev = nr[-1]  # mesmo trecho: uma entrada só
                prev["palavras_antigas"] = prev.get("palavras_antigas", 1) + 1
                prev["word"] += " " + ow["word"]
                if raws[i] != raws[i - 1]:
                    prev["whisper_antigo"] += " " + raws[i]
                prev["end"] = ow["end"]
                prev["trecho_novo"] = trecho(prev["start"], prev["end"])
            else:
                nr.append({"i_antigo": i, "start": ow["start"], "end": ow["end"], "whisper_antigo": raws[i],
                           "word": ow["word"], "motivo": motivo, "trecho_novo": trecho(ow["start"], ow["end"])})
            last = (i, motivo)
            continue
        last = None
        j, sim, dt = assigned[i]
        if raws[i] is None:  # sem bruto, mas a palavra nova já tem o mesmo texto: nada a fazer
            n_unknown_equal += 1
            continue
        final = transfer_edit(raws[i], ow["word"], nraw[j])
        rec = {"i": j, "i_antigo": i, "whisper": nraw[j], "word": final, "start": new_words[j]["start"],
               "dt": round(dt, 3), "sim": round(sim, 2)}
        if final == new_words[j]["word"]:
            out["ja_corretas"].append(rec)
        else:
            new_words[j]["word"] = final
            out["reaplicadas"].append(rec)
    out["reaplicadas"].sort(key=lambda r: r["i"])
    n_nr = sum(r.get("palavras_antigas", 1) for r in nr if r["motivo"] != "sem_bruto")
    out["contagem"] = {"detectadas": len(edits), "reaplicadas": len(out["reaplicadas"]),
                       "ja_corretas": len(out["ja_corretas"]), "nao_reaplicadas": n_nr}
    out["sem_bruto"] = {"palavras": n_unknown_total, "iguais_na_nova": n_unknown_equal,
                        "para_revisar": sum(1 for r in nr if r["motivo"] == "sem_bruto")}
    if aviso:
        out["aviso"] = aviso
    return out


def transcript_seams(tr):
    """(window, overlap) das emendas entre janelas de um transcript: o campo "janelas"; sem ele, os padrões
    (600 s / 1,5 s) se foi gerado por este script, e (0, 0) — sem emendas — se veio de fora (reel-editor e afins
    transcrevem o arquivo numa passada só)."""
    jw = tr.get("janelas") or {}
    if "window" in jw:
        return float(jw["window"] or 0), float(jw.get("overlap") or 0)
    if str(tr.get("generated_by", "")).startswith("reel-banco/03_transcribe.py"):
        return DEFAULT_WINDOW_S, DEFAULT_OVERLAP_S
    return 0.0, 0.0


def backfill_raw(tr):
    """Grava "raw" nas palavras de um transcript que não o têm (importado ou anterior a 2026-10-24), a partir do
    texto dos segmentos (ver raw_from_segments), sem mexer em "word". Assim a detecção de correções fica
    congelada e não depende mais do alinhamento. Regras conservadoras: perto de emenda de janelas, diferença só
    de pontuação/caixa não é correção (raw = word); bloco repartido com correção recebe o bruto inteiro em cada
    palavra (num --replace, vira estrutural); sem correspondente nos segmentos, fica sem "raw" (num --replace,
    vai para revisão). Emendas: ver transcript_seams; transcript de fora sem "janelas" ganha "janelas" = 0/0
    explícito. Devolve {"gravadas", "sem_bruto", "correcoes"}."""
    words = tr.get("words", [])
    seams = transcript_seams(tr)
    if "janelas" not in tr and seams == (0.0, 0.0):
        tr["janelas"] = {"window": 0.0, "overlap": 0.0, "nota": "transcrição de fora, numa passada só: sem emendas"}
    info = old_raw_texts(words, tr.get("segments"), seams)
    raws = list(info["raws"])
    for i in info["emenda"]:
        raws[i] = words[i]["word"]
    for g, whole in info["grupos"]:
        if any(raws[i] is not None and raws[i] != words[i]["word"] for i in g):
            for i in g:
                raws[i] = whole
    n = 0
    for i, w in enumerate(words):
        if "raw" not in w and i in info["de_segmentos"] and raws[i] is not None:
            w["raw"] = raws[i]
            n += 1
    return {"gravadas": n, "sem_bruto": sum(1 for w in words if "raw" not in w),
            "correcoes": sum(1 for w in words if "raw" in w and w["raw"] != w["word"])}


def print_not_carried(carry, backup_name):
    """Lista, no fim do comando, as correções do transcript antigo que não foram reaplicadas."""
    if (carry or {}).get("aviso"):
        bc.warn(carry["aviso"])
    nr = (carry or {}).get("nao_reaplicadas") or []
    if not nr:
        return
    bc.warn(f"{len(nr)} correção(ões) manual(is) de 'word' NÃO reaplicada(s) — revise à mão (transcript.json → "
            f"substitui.nao_reaplicadas; antigo em {backup_name}):")
    for r in nr:
        tn = r["trecho_novo"]
        where = f"#{tn['i'][0]}–#{tn['i'][-1]}" if tn["i"] else "sem palavra nova perto"
        before = "?" if r["whisper_antigo"] is None else r["whisper_antigo"]
        k = r.get("palavras_antigas", 1)
        old_i = f"#{r['i_antigo']}" + (f"–#{r['i_antigo'] + k - 1}" if k > 1 else "")
        bc.log(f"    [{bc.fmt_t(r['start'])}] antigo {old_i} '{before}' -> '{r['word']}' ({r['motivo']})  |  "
               f"novo {where}: '{tn['texto']}'")
    bc.log("    Para cada uma: ache o trecho no transcript.json novo e corrija só 'word' (juntar/separar palavras "
           "também vale). Ao JUNTAR palavras, junte também os 'raw' com espaço (\"raw\": \"não é\"); ao DIVIDIR, "
           "copie o mesmo 'raw' nas duas partes. Depois rode 06b_validate_segments.py.")


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
                         "work/, retranscreve e reaplica as correções de 'word' por alinhamento tolerante (tempo ± "
                         "150 ms + texto bruto parecido); as que não casarem vão para substitui.nao_reaplicadas")
    ap.add_argument("--no-carry-edits", action="store_true",
                    help="com --replace: NÃO reaplica as correções manuais de 'word' do transcript antigo")
    ap.add_argument("--backfill-raw", action="store_true",
                    help="NÃO transcreve: grava 'raw' (texto original do whisper, a partir dos segmentos) nas palavras "
                         "que não o têm, no transcript.json do work/ e do index/ — congela a detecção de correções")
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
    if args.backfill_raw and (args.replace or args.import_transcript):
        bc.die("--backfill-raw não combina com --replace nem --import-transcript")
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
    if args.backfill_raw:
        tgt = [p for p in (out, idx_copy) if p.exists()]
        if not tgt:
            bc.die(f"{args.item_id}: sem transcript.json (work/ nem index/)")
        for p in tgt:
            tr = bc.load_json(p)
            bf = backfill_raw(tr)
            if bf["gravadas"]:
                tr["raw_backfill"] = {"em": bc.now_iso(), "fonte": "segments", **bf}
            bc.save_json(p, tr, compact=True)
            bc.log(f"{args.item_id}: 'raw' gravado em {bf['gravadas']} palavra(s) de {p} ({bf['correcoes']} "
                   f"correção(ões) manual(is) visível(is), {bf['sem_bruto']} sem texto original)")
        return
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
        bf = backfill_raw(tr)  # "raw" a partir dos segmentos: correções já feitas na reel-editor continuam visíveis
        bc.save_json(out, tr, compact=True)
        write_txt(tr, txt)
        bc.log(f"{args.item_id}: transcript importado ({len(tr['words'])} palavras; 'raw' gravado em {bf['gravadas']}, "
               f"{bf['correcoes']} já corrigida(s), {bf['sem_bruto']} sem texto original) -> {out}")
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
          "janelas": {"window": args.window, "overlap": args.overlap},
          "descartes": desc, "words": words, "segments": segments}
    t0 = t_start
    bc.log(f"  descarte: {desc['total']} palavra(s) (silêncio {desc['por_motivo']['silencio']}, prompt "
           f"{desc['por_motivo']['prompt']}); {desc['mantidas_no_speech_alto']} mantida(s) com no_speech alto e "
           f"energia de fala (a regra antiga descartaria {desc['regra_antiga_descartaria']}); piso "
           f"{desc['piso_db']} dB, limiar {desc['limiar_db']} dB")
    carry = None
    if old_tr is not None:
        n_old = len(old_tr.get("words", []))
        tr["substitui"] = {"backup": backup.name, "palavras_antes": n_old, "palavras_depois": len(words),
                           "em": bc.now_iso()}
        if args.no_carry_edits:
            tr["substitui"].update({"reaplicar_correcoes": False, "edicoes_reaplicadas": []})
            bc.log(f"  --replace: {n_old} -> {len(words)} palavras; correções de 'word' NÃO reaplicadas "
                   "(--no-carry-edits)")
        else:
            carry = carry_word_edits(words, old_tr.get("words", []), old_tr.get("segments"),
                                     seams=transcript_seams(old_tr))
            ct, sb = carry["contagem"], carry["sem_bruto"]
            tr["substitui"].update({
                "criterio": (f"correção = word != texto bruto do whisper (fonte: {carry['fonte']}); casa com a palavra "
                             f"nova a <= {CARRY_TOL_S * 1000:.0f} ms (<= {SHORT_TOL_S * 1000:.0f} ms se tiver até 2 "
                             f"letras; início ou fim) e similaridade do bruto >= {CARRY_MIN_SIM}"),
                "correcoes_detectadas": carry["detectadas"], "contagem": ct, "sem_bruto": sb,
                "ignoradas_emenda": carry["ignoradas_emenda"], "edicoes_reaplicadas": carry["reaplicadas"],
                "ja_corretas": carry["ja_corretas"], "nao_reaplicadas": carry["nao_reaplicadas"]})
            if carry.get("aviso"):
                tr["substitui"]["aviso"] = carry["aviso"]
            bc.log(f"  --replace: {n_old} -> {len(words)} palavras; {ct['detectadas']} correção(ões) manual(is) de "
                   f"'word' no transcript antigo (texto bruto: {carry['fonte']}): {ct['reaplicadas']} reaplicada(s), "
                   f"{ct['ja_corretas']} já certa(s) na transcrição nova, {ct['nao_reaplicadas']} NÃO reaplicada(s)"
                   + (f"; {sb['palavras']} palavra(s) sem texto original conhecido ({sb['iguais_na_nova']} iguais na "
                      f"nova, {sb['para_revisar']} para revisar)" if sb["palavras"] else "")
                   + (f"; {carry['ignoradas_emenda']} diferença(s) só de caixa/pontuação na emenda de janelas "
                      "ignorada(s)" if carry["ignoradas_emenda"] else ""))
    bc.save_json(out, tr, compact=True)
    write_txt(tr, txt)
    low = sum(1 for w in words if w["prob"] < 0.5)
    bc.log(f"{args.item_id}: OK {len(words)} palavras ({low} com confiança < 0,5) em {time.time() - t0:.0f} s -> {out}")
    if carry:
        print_not_carried(carry, backup.name)


if __name__ == "__main__":
    main()
