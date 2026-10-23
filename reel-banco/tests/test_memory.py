#!/usr/bin/env python3
"""Teste de memória da transcrição: 1 h de áudio sintético, teto de RSS de 2,5 GB para 03_transcribe.py.

Gera 60 min de áudio "tipo fala" (ruído rosa modulado em sílabas + tom variável, com pausas), mono 16 kHz AAC,
ingere com 02_fetch --local e roda 03_transcribe.py com o modelo real (padrão `small`, ou --model) e
--no-vad (para o whisper processar TODO o áudio, pior caso de memória). Mede o pico de RSS do processo
(ru_maxrss do filho = maior pico entre os processos aguardados; o ffmpeg por janela é pequeno) e também
amostra /proc/<pid>/status (VmHWM) do processo Python durante a execução.

Uso:  python tests/test_memory.py [--minutes 60] [--model small] [--limit-gb 2.5] [--keep]
Demora: ~1 h em 2 vCPU com `small` (--no-vad força o whisper a decodificar todo o áudio; ~9,5 min por janela de 10 min).
"""
import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "scripts"
PY = sys.executable


def make_audio(path: Path, minutes: float):
    dur = int(minutes * 60)
    # "sílabas": ruído rosa com envelope de 4 Hz + vogal (tom 140–260 Hz), pausas de 1 s a cada 9 s
    af = (f"anoisesrc=d={dur}:c=pink:r=16000:a=0.25,"
          f"volume='0.5+0.5*sin(2*PI*4*t)':eval=frame[n];"
          f"aevalsrc='0.2*sin(2*PI*(200+60*sin(2*PI*0.3*t))*t)':d={dur}:s=16000[v];"
          f"[n][v]amix=inputs=2:normalize=0,volume='if(lt(mod(t,10),9),1,0)':eval=frame[a]")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_complex", af, "-map", "[a]",
                    "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "32k", str(path)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--model", default=os.environ.get("REEL_WHISPER_MODEL", "small"))
    ap.add_argument("--limit-gb", type=float, default=2.5)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="reel_banco_mem_"))
    env = dict(os.environ, REEL_BANCO_WORK=str(tmp / "work"), REEL_BANCO_INDEX=str(tmp / "index"),
               PYTHONWARNINGS="ignore")
    try:
        audio = tmp / "live_sintetica_1h.m4a"
        t = time.time()
        make_audio(audio, a.minutes)
        print(f"áudio sintético: {audio} ({audio.stat().st_size / 1e6:.1f} MB, {time.time() - t:.0f} s)")
        r = subprocess.run([PY, str(S / "02_fetch.py"), "--local", str(audio)], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        iid = next(l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("ITEM_ID="))

        peak = {"hwm_kb": 0}
        t = time.time()
        p = subprocess.Popen([PY, str(S / "03_transcribe.py"), iid, "--model", a.model, "--no-vad"], env=env)

        def sample():
            while p.poll() is None:
                try:
                    for line in Path(f"/proc/{p.pid}/status").read_text().splitlines():
                        if line.startswith("VmHWM:"):
                            peak["hwm_kb"] = max(peak["hwm_kb"], int(line.split()[1]))
                except OSError:
                    pass
                time.sleep(0.5)
        th = threading.Thread(target=sample, daemon=True)
        th.start()
        rc = p.wait()
        th.join(timeout=2)
        el = time.time() - t
        ru = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss  # KB no Linux
        peak_gb = max(ru, peak["hwm_kb"]) / 1024 / 1024
        assert rc == 0, f"03_transcribe.py falhou ({rc})"
        tr = json.loads((tmp / "work" / iid / "transcript.json").read_text())
        print(f"transcrição: {el / 60:.1f} min, {len(tr['words'])} palavras, duração {tr['duration']:.0f} s")
        print(f"PICO DE RSS: {peak_gb:.2f} GB (VmHWM {peak['hwm_kb'] / 1024 / 1024:.2f} GB, "
              f"ru_maxrss {ru / 1024 / 1024:.2f} GB) — teto {a.limit_gb} GB")
        assert abs(tr["duration"] - a.minutes * 60) < 2
        assert peak_gb <= a.limit_gb, f"RSS {peak_gb:.2f} GB acima do teto {a.limit_gb} GB"
        print("TESTE DE MEMÓRIA OK")
    finally:
        if a.keep:
            print(f"artefatos em {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
