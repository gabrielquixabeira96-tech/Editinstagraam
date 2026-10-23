#!/usr/bin/env python3
"""06 — Polimento da voz (ffmpeg), preservando a sincronia exata:

  high-pass (80 Hz) -> redução de ruído (arnndn com modelo rnnoise embutido; afftdn se não houver)
  -> de-esser -> [corpo: low-shelf body_db/body_hz] -> EQ de voz (corte leve da "lama" em 250 Hz, presença +2 dB em 4 kHz, ar +1,5 dB
  em 10 kHz) -> compressor suave (2,5:1) -> limitador (-1,5 dBTP).

NÃO normaliza loudness (o reel-editor leva a -14 LUFS); deixa headroom. Duração igual à do vídeo
(apad + corte exato). Qualquer latência de filtro é medida por correlação cruzada com o original e
compensada, para o offset ficar em 0.

Preset de voz: "audio": {"preset": "voz_gabriel"} carrega presets/voz_gabriel.json (chaves do bloco sobrescrevem).
Sem pitch/formant shift: só EQ, de-esser, compressão e (opcional) room_mix.

Entrada: `source` do polish.json. Saída: audio_polished.wav (48 kHz, estéreo, 24 bits).

Uso:
  python scripts/06_audio_polish.py work/<nome>/polish/polish.json
  python scripts/06_audio_polish.py polish.json --out voz.wav --print-filter
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_common as pc  # noqa: E402


def build_chain(a, analysis, filters):
    f = [f"highpass=f={float(a.get('highpass_hz', 80)):.0f}:poles=2"]
    dn = a.get("denoise", "auto")
    nr_db = float(a.get("denoise_db", 12))
    if dn == "auto":
        snr = ((analysis or {}).get("audio") or {}).get("snr_db")
        if snr is not None:
            nr_db = 18 if snr < 20 else (12 if snr < 35 else 6)
        dn = "on"
    used = None
    if dn not in ("off", False) and nr_db > 0:
        if "arnndn" in filters and pc.RNNOISE_MODEL.exists():
            mix = pc.clamp(nr_db / 18.0, 0.3, 1.0)
            f.append(f"arnndn=m={pc.ff_filter_value(pc.RNNOISE_MODEL)}:mix={mix:.2f}")
            used = f"arnndn mix {mix:.2f}"
        else:
            f.append(f"afftdn=nr={nr_db:.0f}:nf=-40:tn=1")
            used = f"afftdn nr {nr_db:.0f} dB"
    de = pc.clamp(float(a.get("deesser", 0.35)), 0, 1)
    if de > 0:
        f.append(f"deesser=i={de:.2f}:m=0.5:f=0.5:s=o")
    body = float(a.get("body_db", 0.0))            # corpo/calor: low-shelf (o high-pass acima segura < 80 Hz)
    body_hz = float(a.get("body_hz", 150))
    if abs(body) > 0.05:
        f.append(f"lowshelf=f={body_hz:.0f}:g={body:.1f}:w=0.7")
    mud = float(a.get("mud_db", -2.0))             # "lama"/"caixa" (carro: ~300–500 Hz)
    mud_hz = float(a.get("mud_hz", 250))
    if abs(mud) > 0.05:
        f.append(f"equalizer=f={mud_hz:.0f}:t=q:w={float(a.get('mud_q', 1.2)):.2f}:g={mud:.1f}")
    pres = float(a.get("presence_db", 2.0))
    pres_hz = float(a.get("presence_hz", 4000))
    if abs(pres) > 0.05:
        f.append(f"equalizer=f={pres_hz:.0f}:t=q:w=1.0:g={pres:.1f}")
    air = float(a.get("air_db", 1.5))
    if abs(air) > 0.05:
        f.append(f"treble=g={air:.1f}:f={float(a.get('air_hz', 10000)):.0f}:w=0.5")
    ratio = float(a.get("comp_ratio", 2.5))
    thr = float(a.get("comp_threshold_db", -20))
    if ratio > 1.01:
        f.append(f"acompressor=threshold={thr:.0f}dB:ratio={ratio:.2f}:attack={float(a.get('comp_attack_ms', 12)):.0f}"
                 f":release={float(a.get('comp_release_ms', 150)):.0f}:knee=4:makeup=1")
    room = float(a.get("room_mix", 0.0))           # ambiência curtíssima (0 = nada)
    if room > 0:
        f.append(f"aecho=1.0:{room:.2f}:{float(a.get('room_ms', 18)):.0f}:0.25")
    lim = float(a.get("limiter_db", -1.5))
    f.append(f"alimiter=limit={lim:.1f}dB:attack=5:release=50:level=false")
    return ",".join(f), {"denoise": used, "deesser": de, "body_db": body, "body_hz": body_hz, "mud_db": mud,
                        "mud_hz": mud_hz, "presence_db": pres, "presence_hz": pres_hz, "air_db": air,
                        "comp": {"ratio": ratio, "threshold_db": thr}, "room_mix": room, "limiter_db": lim}


def measure_lag(src, out, sr=16000):
    """Lag (s) do processado em relação ao original (positivo = atrasado).
    Correlação cruzada da forma de onda filtrada em 300–3000 Hz (faixa da voz, pouco afetada pelo EQ),
    em até 8 trechos de 2 s nos pontos mais altos; devolve a mediana. Resolução: 1/16000 s."""
    from scipy.signal import butter, sosfiltfilt
    a = pc.decode_audio(src, sr=sr, channels=1)[:, 0]
    b = pc.decode_audio(out, sr=sr, channels=1)[:, 0]
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if n < sr:
        return 0.0
    sos = butter(4, [300, 3000], btype="band", fs=sr, output="sos")
    a, b = sosfiltfilt(sos, a).astype(np.float32), sosfiltfilt(sos, b).astype(np.float32)
    L, R = 2 * sr, int(0.05 * sr)
    hop = sr // 2
    ener = [(float(np.dot(a[i:i + L], a[i:i + L])), i) for i in range(0, max(1, n - L), hop)]
    ener.sort(reverse=True)
    lags = []
    for e, i in ener[:8]:
        if e <= 1e-6:
            continue
        ref = a[i:i + L]
        lo = max(0, i - R)
        win = b[lo:i + L + R]
        if len(win) < L:
            continue
        c = np.correlate(win, ref, mode="valid")
        k = int(np.argmax(c))
        lags.append((lo + k - i) / sr)
    if not lags:
        return 0.0
    return float(np.median(lags))


def main():
    ap = argparse.ArgumentParser(description="Polimento de voz sem alterar a sincronia.")
    ap.add_argument("polish")
    ap.add_argument("--workdir")
    ap.add_argument("--in", dest="inp", help="entrada (padrão: source do polish.json)")
    ap.add_argument("--out", help="saída WAV (padrão: audio_polished.wav)")
    ap.add_argument("--print-filter", action="store_true")
    args = ap.parse_args()

    cfg_path = Path(args.polish).resolve()
    cfg = pc.load_polish(cfg_path)
    workdir = Path(args.workdir).resolve() if args.workdir else cfg_path.parent
    P = pc.paths_in(workdir)
    src = Path(args.inp) if args.inp else pc.polish_source(cfg, cfg_path)
    out = Path(args.out) if args.out else P["audio"]
    info = pc.probe(src)
    dur = info["duration"] or 0.0
    analysis = pc.load_json(P["analysis"]) if P["analysis"].exists() else {}
    a = pc.resolve_audio_preset(cfg.get("audio") or {})
    if not info["has_audio"]:
        pc.log("origem sem áudio — gerando silêncio da mesma duração")
        pc.run(["ffmpeg", "-y", "-f", "lavfi", "-t", f"{dur:.6f}", "-i", f"anullsrc=r={pc.AUDIO_SR}:cl=stereo",
                "-c:a", "pcm_s24le", out])
        pc.save_json(workdir / "audio.json", {"enabled": False, "reason": "sem áudio"})
        return
    base = f"aresample={pc.AUDIO_SR}:async=1:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if not a.get("enabled", True):
        chain, info_f = base, {"enabled": False}
    else:
        c, info_f = build_chain(a, analysis, pc.ffmpeg_filters())
        chain = base + "," + c
        info_f["enabled"] = True
    if args.print_filter:
        print(chain)
        return
    tmp = out.with_suffix(".tmp.wav")
    pc.log(f"polindo áudio de {src.name}: {info_f}")
    pc.run(["ffmpeg", "-y", "-i", src, "-vn", "-af", chain + ",apad", "-t", f"{dur:.6f}",
            "-c:a", "pcm_s24le", "-ar", str(pc.AUDIO_SR), tmp])
    lag = measure_lag(src, tmp) if a.get("enabled", True) else 0.0
    if abs(lag) >= 0.0005:
        pc.log(f"  latência de filtro medida: {lag * 1000:+.1f} ms — compensando")
        if lag > 0:
            af = f"atrim=start={lag:.6f},asetpts=PTS-STARTPTS,apad"
        else:
            af = f"adelay={int(round(-lag * 1000))}|{int(round(-lag * 1000))},apad"
        pc.run(["ffmpeg", "-y", "-i", tmp, "-af", af, "-t", f"{dur:.6f}", "-c:a", "pcm_s24le", out])
        tmp.unlink(missing_ok=True)
        lag2 = measure_lag(src, out)
    else:
        tmp.replace(out)
        lag2 = lag
    peak = float(np.abs(pc.decode_audio(out, sr=16000, channels=1)).max())
    pc.save_json(workdir / "audio.json", {**info_f, "filter": chain, "lag_ms_before": round(lag * 1000, 2),
                                           "lag_ms_after": round(lag2 * 1000, 2), "peak_dbfs": round(20 * np.log10(peak + 1e-9), 2)})
    pc.log(f"OK: {out}  (offset final {lag2 * 1000:+.1f} ms, pico {20 * np.log10(peak + 1e-9):.1f} dBFS)")


if __name__ == "__main__":
    main()
