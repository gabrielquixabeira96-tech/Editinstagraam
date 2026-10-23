#!/usr/bin/env python3
"""04 — Cortes de cena e medidas por cena de um item de vídeo -> work/banco/<item_id>/scenes.json.

Uma única decodificação do vídeo (ffmpeg): scale 480 px -> scdet (cortes) -> amostra 2 quadros/s (rawvideo
em pipe) para medir, por quadro: rosto presente e tamanho (YuNet da reel-polish), nitidez (variância do
Laplaciano), exposição (luma média, % estourado / % esmagado). Áudio decodificado em fluxo a 8 kHz: RMS em
janelas de 100 ms -> nível médio (dBFS) e fração de silêncio (< -45 dBFS) por cena.

Cenas mais longas que --max-scene (padrão 30 s; comum em live/aula com câmera fixa) são subdivididas em
blocos ("corte": "bloco") para que as medidas continuem locais. Cenas menores que --min-scene são fundidas.

scenes.json:
  {"item_id", "duracao", "params", "scenes": [{"n", "t0", "t1", "dur", "corte": "cena|bloco|inicio",
    "rosto": fração 0-1, "rosto_tam": largura média do rosto / largura do quadro, "nitidez": mediana,
    "luma": 0-255, "estourado": fração, "escuro": fração, "audio_db": dBFS, "silencio": fração,
    "sug_visual": 0-5, "sug_audio": 0-5}]}
As notas sugeridas são heurísticas — o Claude decide qualidade_visual/qualidade_audio em segments.json.

Uso:
  python scripts/04_scenes.py <item_id> [--threshold 10] [--min-scene 1.0] [--max-scene 30] [--fps 2] [--force]
"""
import argparse
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_common as bc  # noqa: E402

AW = 480  # largura de análise


class Faces:
    def __init__(self, w, h):
        import cv2
        self.cv2 = cv2
        self.det = None
        m = bc.yunet_model()
        if m.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.det = cv2.FaceDetectorYN.create(str(m), "", (w, h), 0.6, 0.3, 5000)
            except Exception as e:  # pragma: no cover
                bc.warn(f"YuNet indisponível ({e}) — rosto não será medido")
        else:
            bc.warn(f"modelo YuNet não encontrado em {m} — rosto não será medido")

    def biggest(self, bgr):
        if self.det is None:
            return None
        _, faces = self.det.detect(bgr)
        if faces is None or not len(faces):
            return 0.0
        return float(max(f[2] for f in faces)) / bgr.shape[1]


def audio_rms_db(src, sr=8000, win_s=0.1):
    """RMS em janelas de 100 ms (dBFS) via ffmpeg em fluxo; devolve array (ou None se não houver áudio)."""
    p = subprocess.Popen([bc.ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(src), "-vn", "-ac", "1",
                          "-ar", str(sr), "-f", "s16le", "-"], stdout=subprocess.PIPE)
    win = int(sr * win_s)
    out, buf = [], b""
    while True:
        chunk = p.stdout.read(win * 2 * 600)
        if not chunk:
            break
        buf += chunk
        n = len(buf) // (win * 2)
        if n:
            a = np.frombuffer(buf[:n * win * 2], np.int16).astype(np.float32).reshape(n, win) / 32768.0
            out.append(20 * np.log10(np.sqrt(np.mean(a * a, axis=1)) + 1e-9))
            buf = buf[n * win * 2:]
    p.wait()
    if not out:
        return None
    return np.concatenate(out)


def score_visual(s):
    """0-5 heurístico: nitidez, exposição e (se houver) rosto bem enquadrado."""
    sc = 2.5
    sh = s["nitidez"]
    sc += 1.0 if sh > 250 else 0.5 if sh > 120 else -0.5 if sh < 40 else 0
    if 70 <= s["luma"] <= 180:
        sc += 0.5
    else:
        sc -= 0.75
    if s["estourado"] > 0.08 or s["escuro"] > 0.25:
        sc -= 0.75
    if s["rosto"] >= 0.7 and s["rosto_tam"] >= 0.18:
        sc += 1.0
    elif s["rosto"] >= 0.5:
        sc += 0.5
    return round(max(0.0, min(5.0, sc)), 1)


def score_audio(s):
    if s["audio_db"] is None:
        return 0.0
    db, sil = s["audio_db"], s["silencio"]
    sc = 3.0
    sc += 1.0 if -28 <= db <= -12 else 0.0 if -36 <= db <= -8 else -1.5
    if db > -6:
        sc -= 1.0  # provável saturação
    sc -= 1.5 * max(0.0, sil - 0.35)
    return round(max(0.0, min(5.0, sc)), 1)


def main():
    ap = argparse.ArgumentParser(description="Cortes de cena e medidas por cena (rosto, nitidez, exposição, áudio).")
    ap.add_argument("item_id")
    ap.add_argument("--threshold", type=float, default=10.0, help="limiar do scdet (0-100; menor = mais cortes)")
    ap.add_argument("--min-scene", type=float, default=1.0, help="funde cenas menores que isto (s)")
    ap.add_argument("--max-scene", type=float, default=30.0, help="subdivide cenas maiores que isto em blocos (s)")
    ap.add_argument("--fps", type=float, default=2.0, help="quadros/s amostrados para as medidas")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    bc.set_log_item(args.item_id)
    item = bc.load_item(args.item_id)
    if item.get("tipo") != "video":
        bc.die(f"{args.item_id} não é vídeo")
    src = bc.find_source(args.item_id)
    if not src:
        bc.die(f"mídia ausente (rode 02_fetch.py --item {args.item_id} --refetch)")
    d = bc.item_dir(args.item_id)
    out = d / "scenes.json"
    params = {"threshold": args.threshold, "min_scene": args.min_scene, "max_scene": args.max_scene, "fps": args.fps}
    st = bc.State(args.item_id)
    signature = bc.sig([bc.file_sig(src), params, 2])
    if not args.force and st.fresh("scenes", signature, [out]):
        bc.log(f"{args.item_id}: scenes.json em cache")
        return

    dur = float(item.get("duracao") or bc.probe(src).get("duration") or 0)
    has_video = item.get("largura") is not None
    frames = []  # (t, face, face_size, sharp, luma, hi, lo)
    cuts = []
    if has_video:
        W0, H0 = item.get("largura") or 1080, item.get("altura") or 1920
        aw = AW if W0 >= H0 else 360  # vertical: 360 px de largura
        ah = int(round(H0 * aw / W0 / 2) * 2)
        scfile = d / "scdet.txt"
        if scfile.exists():
            scfile.unlink()
        # nome RELATIVO + cwd=pasta do item: caminhos com ' ou : quebrariam o filtergraph
        vf = (f"scale={aw}:{ah},scdet=threshold={args.threshold},"
              f"metadata=mode=print:key=lavfi.scd.time:file=scdet.txt,fps={args.fps},format=bgr24")
        bc.log(f"{args.item_id}: decodificando {bc.fmt_t(dur)} para cortes e medidas ({aw}x{ah}, {args.fps} q/s) ...")
        p = subprocess.Popen([bc.ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(src.resolve()), "-an", "-vf", vf,
                              "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, cwd=str(d))
        import cv2
        faces = Faces(aw, ah)
        fsz = aw * ah * 3
        i = 0
        while True:
            b = p.stdout.read(fsz)
            if len(b) < fsz:
                break
            img = np.frombuffer(b, np.uint8).reshape(ah, aw, 3)
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
            luma = float(g.mean())
            hi = float((g >= 250).mean())
            lo = float((g <= 8).mean())
            fs = faces.biggest(img)
            frames.append((i / args.fps, fs, sharp, luma, hi, lo))
            i += 1
        p.wait()
        if scfile.exists():
            for line in scfile.read_text().splitlines():
                if "lavfi.scd.time=" in line:
                    try:
                        cuts.append(float(line.split("=", 1)[1]))
                    except ValueError:
                        pass
        bc.log(f"  {len(frames)} quadros medidos, {len(cuts)} cortes brutos")
    rms = audio_rms_db(src) if item.get("tem_audio", True) else None

    # monta cenas
    bounds = [0.0] + sorted(c for c in cuts if 0 < c < dur) + [dur]
    merged = [bounds[0]]
    for b in bounds[1:-1]:
        if b - merged[-1] >= args.min_scene:
            merged.append(b)
    if dur - merged[-1] < args.min_scene and len(merged) > 1:
        merged.pop()
    merged.append(dur)
    spans = []
    for k in range(len(merged) - 1):
        a, b = merged[k], merged[k + 1]
        kind = "inicio" if k == 0 else "cena"
        if b - a > args.max_scene:
            n = math.ceil((b - a) / args.max_scene)
            step = (b - a) / n
            for j in range(n):
                spans.append((a + j * step, a + (j + 1) * step, kind if j == 0 else "bloco"))
        else:
            spans.append((a, b, kind))

    scenes = []
    fr = np.array([(f[0], -1 if f[1] is None else f[1], f[2], f[3], f[4], f[5]) for f in frames]) if frames else None
    for n, (a, b, kind) in enumerate(spans, 1):
        s = {"n": n, "t0": round(a, 3), "t1": round(b, 3), "dur": round(b - a, 3), "corte": kind}
        if fr is not None:
            m = (fr[:, 0] >= a) & (fr[:, 0] < b)
            sub = fr[m] if m.any() else fr[np.argmin(np.abs(fr[:, 0] - (a + b) / 2))][None, :]
            fcol = sub[:, 1]
            if (fcol < 0).all():
                s["rosto"], s["rosto_tam"] = None, None
            else:
                s["rosto"] = round(float((fcol > 0).mean()), 2)
                s["rosto_tam"] = round(float(fcol[fcol > 0].mean()) if (fcol > 0).any() else 0.0, 3)
            s["nitidez"] = round(float(np.median(sub[:, 2])), 1)
            s["luma"] = round(float(sub[:, 3].mean()), 1)
            s["estourado"] = round(float(sub[:, 4].mean()), 3)
            s["escuro"] = round(float(sub[:, 5].mean()), 3)
        else:
            s.update({"rosto": None, "rosto_tam": None, "nitidez": 0.0, "luma": 0.0, "estourado": 0.0, "escuro": 0.0})
        if rms is not None and len(rms):
            ia, ib = int(a / 0.1), max(int(a / 0.1) + 1, int(b / 0.1))
            seg = rms[ia:ib]
            if len(seg):
                lin = np.mean(10 ** (seg / 10))
                s["audio_db"] = round(float(10 * np.log10(lin + 1e-12)), 1)
                s["silencio"] = round(float((seg < -45).mean()), 2)
            else:
                s["audio_db"], s["silencio"] = None, None
        else:
            s["audio_db"], s["silencio"] = None, None
        s2 = dict(s)
        for k in ("rosto", "rosto_tam"):
            s2[k] = s2[k] or 0.0
        s["sug_visual"] = score_visual(s2) if fr is not None else 0.0
        s["sug_audio"] = score_audio(s)
        scenes.append(s)

    bc.save_json(out, {"item_id": args.item_id, "duracao": dur, "params": params, "gerado_em": bc.now_iso(),
                       "scenes": scenes})
    st.done("scenes", signature, [out], n=len(scenes))
    item["n_cenas"] = len(scenes)
    bc.save_item(item)
    bc.log(f"{args.item_id}: OK {len(scenes)} cenas/blocos -> {out}")


if __name__ == "__main__":
    main()
