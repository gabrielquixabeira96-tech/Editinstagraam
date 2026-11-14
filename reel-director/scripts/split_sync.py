#!/usr/bin/env python3
"""split_sync.py — Reel 9:16 em tela dividida: gravação de tela/artefato (em cima, sem escala) sincronizada à
fala do Gabriel (embaixo, quadro horizontal INTEIRO em largura total, sem zoom/recorte), com cortes de pausa/
repetição, legenda serifada em caixa quadrada (palavra falada branca, por falar cinza), rótulos de capítulo,
trilha discreta com ducking e loudness -14 LUFS.

Uso: python3 split_sync.py plan.json saida.mp4 [--preview SEG] [--from-step mix|compose]

plan.json (tempos da fala na timeline ORIGINAL do vídeo do rosto):
  face, transcript (transcript.json da reel-editor), screen (vídeo 2, 1080 de largura), screen_crop_y
  groups: [[w_ini, w_fim], ...]   índices de palavras de cada trecho mantido (pausas entre grupos são cortadas)
  word_overrides: {"92": {"end": 40.82}}  ajusta limites de palavra (ex.: cortar tomada repetida)
  anchors: [[ref, t_tela], ...]   ref = gNs/gNe (início/fim do grupo N), wN (início da palavra N), end;
            t_tela = segundo do vídeo 2 que deve estar na tela naquele instante. Interpolação linear entre
            âncoras; duas âncoras no mesmo instante = salto (com dissolve curto).
  captions: [[w_ini, w_fim, "linha 1|linha 2"], ...]
  labels: [{"type": "name"|"chapter", "at": ref, "dur": s, ...}]
  music, music_gain_db, tail_s, pad_in, pad_out
"""
import json, os, subprocess, sys, re, math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FPS = 30
W, H = 1080, 1920
SPLIT = 1180            # topo do painel do rosto
FACE_H = 608            # 1920x1080 -> 1080x608 (escala uniforme, quadro inteiro)
FONT_DIR = os.path.expanduser('~/.reel-fonts')
SERIF = os.path.join(FONT_DIR, 'CormorantGaramond-600-normal.ttf')
SERIF_IT = os.path.join(FONT_DIR, 'CormorantGaramond-600-italic.ttf')
SANS = '/usr/share/fonts/opentype/inter/Inter-Medium.otf'
IVORY = (244, 241, 234); GREY = (172, 168, 160); BRASS = (176, 141, 87); CHAR = (14, 13, 11)

plan_path, out = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
PREVIEW = float(args[args.index('--preview') + 1]) if '--preview' in args else None
FROM = args[args.index('--from-step') + 1] if '--from-step' in args else None
P = json.load(open(plan_path)); WD = os.path.dirname(os.path.abspath(plan_path))
def rp(p): return p if os.path.isabs(p) else os.path.join(WD, p)
words = json.load(open(P['transcript']))['words']
for k, v in P.get('word_overrides', {}).items():
    words[int(k)].update(v)

# ---------- 1. trechos mantidos (alinhados à grade de quadros) ----------
fr = lambda t: round(t * FPS) / FPS
keeps = []
for gi, (a, b) in enumerate(P['groups']):
    s = words[a]['start'] - P.get('pad_in', 0.1)
    e = words[b]['end'] + P.get('pad_out', 0.14)
    if keeps: s = max(s, keeps[-1][1] + 0.02)
    if gi + 1 < len(P['groups']):
        e = min(e, words[P['groups'][gi + 1][0]]['start'] - 0.06)
    keeps.append([fr(s), fr(e)])
tail = P.get('tail_s', 0.8)
keeps[-1][1] = fr(keeps[-1][1] + tail)
offs, acc = [], 0.0
for s, e in keeps: offs.append(acc); acc += e - s
DUR = acc; NF = int(round(DUR * FPS))
def omap(t):
    """tempo original -> tempo final (None se cortado)"""
    for (s, e), o in zip(keeps, offs):
        if s - 1e-6 <= t <= e + 1e-6: return o + (t - s)
    return None
def ref(r):
    if r == 'end': return DUR
    m = re.match(r'g(\d+)([se])$', r)
    if m:
        i = int(m.group(1)); return offs[i] if m.group(2) == 's' else offs[i] + keeps[i][1] - keeps[i][0]
    m = re.match(r'w(\d+)$', r)
    t = omap(words[int(m.group(1))]['start']); assert t is not None, r; return t
print(f'trechos: {len(keeps)}  duração final {DUR:.2f}s ({NF} quadros)')

# ---------- 2. mapa de tempo da tela ----------
AN = sorted([(round(ref(r), 5), float(v)) for r, v in P['anchors']], key=lambda x: x[0])  # sort estável: ordem do plano decide empates
def screen_t(t):
    seg = None
    for i in range(len(AN) - 1):
        (x0, y0), (x1, y1) = AN[i], AN[i + 1]
        if x1 - x0 < 1e-6: continue
        if x0 - 1e-6 <= t < x1: seg = (x0, y0, x1, y1)
    if seg is None:
        return AN[-1][1] if t >= AN[-1][0] else AN[0][1]
    x0, y0, x1, y1 = seg
    return y0 + (y1 - y0) * (t - x0) / (x1 - x0)
sidx = [min(int(screen_t(f / FPS) * FPS), 10 ** 9) for f in range(NF)]
bad=[(f,a,b) for f,(a,b) in enumerate(zip(sidx,sidx[1:])) if b<a]
if bad: print("AN",AN); print("não monotônico em",bad[:5]); sys.exit(1)
for i in range(len(AN) - 1):
    (x0, y0), (x1, y1) = AN[i], AN[i + 1]
    if x1 - x0 > 1e-6: print(f'  tela {y0:6.2f}-{y1:6.2f}s  -> final {x0:6.2f}-{x1:6.2f}s  vel {(y1 - y0) / (x1 - x0):.2f}x')

# ---------- 3. corte do rosto + voz ----------
face_cut = os.path.join(WD, 'face_cut.mkv'); voice = os.path.join(WD, 'voice_cut.wav')
if FROM not in ('mix', 'compose'):
    # vídeo: seleção exata por número de quadro (fonte 30 fps CFR), uma passada, pouca memória
    expr = '+'.join(f'between(n,{int(round(s * FPS))},{int(round(e * FPS)) - 1})' for s, e in keeps)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', P['face'], '-an', '-vf',
                    f"select='{expr}',setpts=N/{FPS}/TB,scale={W}:{FACE_H}:flags=lanczos", '-fps_mode', 'passthrough',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '12', '-pix_fmt', 'yuv420p', face_cut], check=True)
    # áudio: fatias exatas em amostras + fade de 12 ms nas emendas (sem clique)
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', P['face'], '-vn', '-ac', '2', '-ar', '48000', '-f', 's16le', '-'],
                         capture_output=True, check=True).stdout
    A = np.frombuffer(raw, np.int16).reshape(-1, 2).astype(np.float32); parts = []
    fl = int(0.012 * 48000); ramp = np.linspace(0, 1, fl)[:, None]
    for s, e in keeps:
        n0 = int(round(s * FPS)) * 1600; n1 = int(round(e * FPS)) * 1600
        x = A[n0:n1].copy()
        if len(x) < n1 - n0: x = np.vstack([x, np.zeros((n1 - n0 - len(x), 2), np.float32)])
        x[:fl] *= ramp; x[-fl:] *= ramp[::-1]; parts.append(x)
    import wave
    wv = wave.open(voice, 'wb'); wv.setnchannels(2); wv.setsampwidth(2); wv.setframerate(48000)
    wv.writeframes(np.clip(np.vstack(parts), -32768, 32767).astype(np.int16).tobytes()); wv.close()
    print('rosto/voz cortados')

# ---------- 4. mix: voz + trilha com ducking, -14 LUFS ----------
mix = os.path.join(WD, 'mix.wav')
if FROM != 'compose':
    mg = P.get('music_gain_db', -21)
    if P.get('music'):
        fcm = (f'[1:a]atrim=0:{DUR:.3f},volume={mg}dB,afade=t=in:d=1.2,afade=t=out:st={DUR - 2.0:.3f}:d=2.0[m];'
               f'[0:a]asplit[vo][sc];[m][sc]sidechaincompress=threshold=0.04:ratio=6:attack=25:release=400[duck];'
               f'[vo][duck]amix=inputs=2:normalize=0:duration=first,afade=t=out:st={DUR - 0.6:.3f}:d=0.6[x]')
        ins = ['-i', voice, '-i', rp(P['music'])]
    else:
        fcm = f'[0:a]afade=t=out:st={DUR - 0.6:.3f}:d=0.6[x]'; ins = ['-i', voice]
    pre = os.path.join(WD, 'mix_pre.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *ins, '-filter_complex', fcm, '-map', '[x]', '-ar', '48000', pre], check=True)
    m = subprocess.run(['ffmpeg', '-hide_banner', '-i', pre, '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    j = json.loads(m[m.rindex('{'):m.rindex('}') + 1])
    ln = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', pre, '-af', ln + ',alimiter=limit=0.80:level=false:attack=3:release=80',
                    '-ar', '48000', mix], check=True)
    print('mix ok')

def final_audio_fix(path):
    """mede o MP4 (após AAC) e corrige para -14 ±0,5 LUFS e pico real < -1 dBTP, copiando o vídeo"""
    for _ in range(3):
        m = subprocess.run(['ffmpeg', '-hide_banner', '-i', path, '-af', 'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True).stderr
        I = float(re.findall(r'I:\s+(-?[\d.]+) LUFS', m)[-1]); tp = float(re.findall(r'Peak:\s+(-?[\d.]+) dBFS', m)[-1])
        print(f'áudio final: {I} LUFS, pico {tp} dBTP')
        if abs(I + 14) <= 0.5 and tp < -1.0: return
        tmp = path + '.tmp.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', path, '-c:v', 'copy', '-af',
                        f'volume={-14 - I:.2f}dB,alimiter=limit=0.78:level=false:attack=3:release=80', '-c:a', 'aac', '-b:a', '192k',
                        '-movflags', '+faststart', tmp], check=True); os.replace(tmp, path)
if '--audio-only' in args:
    tmp = out + '.tmp.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', out, '-i', mix, '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac',
                    '-b:a', '192k', '-movflags', '+faststart', tmp], check=True); os.replace(tmp, out)
    final_audio_fix(out); sys.exit(0)

# ---------- 5. gráficos ----------
def font(p, s): return ImageFont.truetype(p, s)
F_CAP = font(SERIF, 62)
def spaced(d, xy, text, f, fill, track):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill); x += d.textlength(ch, font=f) + track
    return x
def spaced_w(d, text, f, track): return sum(d.textlength(c, font=f) + track for c in text) - track

# legendas: cada palavra exibida recebe um instante (proporcional às palavras do Whisper no trecho)
caps = []
for a, b, txt in P['captions']:
    ws = [w for i, w in enumerate(words[a:b + 1], a) if omap(w['start']) is not None]
    t0 = omap(ws[0]['start']); t1 = omap(ws[-1]['end'])
    lines = [l.split() for l in txt.split('|')]
    flat = [w for l in lines for w in l]
    times = []
    for k in range(len(flat)):
        j = min(len(ws) - 1, int(k * len(ws) / len(flat)))
        times.append(omap(ws[j]['start']))
    caps.append({'t0': t0, 't1': t1, 'lines': lines, 'times': times})
for i, c in enumerate(caps):   # cada legenda fica até a próxima (máx. 0,5 s após a fala)
    nxt = caps[i + 1]['t0'] if i + 1 < len(caps) else DUR
    c['end'] = min(nxt, c['t1'] + 0.5)

def render_cap(c, t):
    tmp = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    sp = tmp.textlength(' ', font=F_CAP)
    lw = [sum(tmp.textlength(w, font=F_CAP) for w in l) + sp * (len(l) - 1) for l in c['lines']]
    lh = 70; padx, pady = 34, 20
    bw = int(max(lw) + 2 * padx); bh = int(lh * len(c['lines']) + 2 * pady - 6)
    im = Image.new('RGBA', (bw, bh), (10, 10, 10, 236)); d = ImageDraw.Draw(im)
    d.line([(0, 0), (bw, 0)], fill=BRASS + (200,), width=1)
    k = 0
    for li, l in enumerate(c['lines']):
        x = (bw - lw[li]) / 2; y = pady - 14 + li * lh
        for w in l:
            col = IVORY if t >= c['times'][k] - 0.03 else GREY
            d.text((x, y), w, font=F_CAP, fill=col); x += tmp.textlength(w, font=F_CAP) + sp; k += 1
    return im

F_T = font(SERIF, 46); F_TI = font(SERIF_IT, 46); F_S = font(SANS, 21)
def render_label(L):
    im = Image.new('RGBA', (900, 150), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    if L['type'] == 'name':
        title, sub = L['title'], L['sub'].upper()
        tw = d.textlength(title, font=F_T); sw = spaced_w(d, sub, F_S, 6)
        bw = int(max(tw, sw) + 56); bh = 124
        d.rectangle([0, 0, bw, bh], fill=(10, 10, 10, 170))
        d.line([(28, 22), (88, 22)], fill=BRASS + (255,), width=2)
        d.text((28, 30), title, font=F_T, fill=IVORY)
        spaced(d, (29, 88), sub, F_S, (200, 196, 188), 6)
    else:
        num = L['num']; txt = L['text'].upper()
        nw = d.textlength(num, font=F_TI); tw = spaced_w(d, txt, F_S, 7)
        bw = int(28 + nw + 22 + tw + 30); bh = 76
        d.rectangle([0, 0, bw, bh], fill=(10, 10, 10, 170))
        d.text((28, 6), num, font=F_TI, fill=BRASS + (255,))
        d.line([(28 + nw + 11, 22), (28 + nw + 11, 54)], fill=BRASS + (200,), width=1)
        spaced(d, (28 + nw + 22, 26), txt, F_S, IVORY, 7)
    return im.crop((0, 0, bw + 1, bh + 1))
labels = []
for L in P.get('labels', []):
    labels.append({'t0': ref(L['at']), 'dur': L['dur'], 'img': render_label(L)})

def ease(x): x = min(1, max(0, x)); return 1 - (1 - x) ** 3
def paste(canvas, im, x, y, alpha=1.0):
    if alpha <= 0: return
    if alpha < 1:
        a = im.getchannel('A').point(lambda v: int(v * alpha)); im = im.copy(); im.putalpha(a)
    canvas.alpha_composite(im, (int(x), int(y)))

# ---------- 6. composição quadro a quadro ----------
def reader(cmd, fsize):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=fsize * 4)
    return p
SC_H = SPLIT
CY0, CY1 = P.get('screen_crop', [300, 1600])
SW = int(round(W * SC_H / (CY1 - CY0) / 2) * 2); SX = (W - SW) // 2
scr = reader(['ffmpeg', '-v', 'error', '-i', P['screen'], '-vf',
              f'crop={W}:{CY1 - CY0}:0:{CY0},scale={SW}:{SC_H}:flags=lanczos,pad={W}:{SC_H}:{SX}:0,fps={FPS}',
              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], W * SC_H * 3)
fac = reader(['ffmpeg', '-v', 'error', '-i', face_cut, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], W * FACE_H * 3)
nf = NF if PREVIEW is None else int(PREVIEW * FPS)
enc_out = out
enc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                        '-i', mix, '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-profile:v', 'high',
                        '-pix_fmt', 'yuv420p', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                        '-c:a', 'aac', '-b:a', '192k', '-t', f'{nf / FPS:.3f}', '-movflags', '+faststart', enc_out], stdin=subprocess.PIPE)
cur_i, cur = -1, None
def screen_frame(i):
    global cur_i, cur
    while cur_i < i:
        b = scr.stdout.read(W * SC_H * 3)
        if len(b) < W * SC_H * 3: break
        fr_ = np.frombuffer(b, np.uint8).reshape(SC_H, W, 3).copy()
        if SX > 0:   # laterais: extensão da borda (papel ou fundo escuro do próprio artefato, sem barra)
            fr_[:, :SX] = fr_[:, SX:SX + 1]; fr_[:, SX + SW:] = fr_[:, SX + SW - 1:SX + SW]
        cur = fr_; cur_i += 1
    return cur
grain_rng = np.random.default_rng(5)
band_h = H - SPLIT - FACE_H
prev_scr, xfade_left, XF = None, 0, 6
last_idx = None
for f in range(nf):
    t = f / FPS
    i = sidx[f]
    if last_idx is not None and i - last_idx > 12:      # salto na tela -> dissolve curto
        xfade_left = XF; prev_scr = s_img.copy()
    s_img = screen_frame(i).copy(); last_idx = i
    if xfade_left > 0:
        k = 1 - xfade_left / (XF + 1)
        s_img = (prev_scr * (1 - k) + s_img * k).astype(np.uint8); xfade_left -= 1
    fb = fac.stdout.read(W * FACE_H * 3)
    if len(fb) == W * FACE_H * 3: face = np.frombuffer(fb, np.uint8).reshape(FACE_H, W, 3)
    canvas = np.empty((H, W, 3), np.uint8)
    canvas[:SPLIT] = s_img
    canvas[SPLIT:SPLIT + FACE_H] = face
    # faixa inferior: reflexo desfocado e escurecido do próprio quadro (sem texto: zona de UI do Instagram)
    small = Image.fromarray(face[-band_h:][::-1]).resize((W // 12, max(1, band_h // 12)), Image.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(2)).resize((W, band_h), Image.BILINEAR)
    bnd = np.asarray(small).astype(np.float32)
    grad = np.linspace(0.30, 0.10, band_h)[:, None, None]
    bnd = bnd * grad + np.array(CHAR, np.float32) * (1 - grad)
    bnd += grain_rng.normal(0, 3.0, bnd.shape[:2])[:, :, None]
    canvas[SPLIT + FACE_H:] = np.clip(bnd, 0, 255).astype(np.uint8)
    img = Image.fromarray(canvas).convert('RGBA')
    d = ImageDraw.Draw(img)
    # filetes de latão nas emendas
    d.line([(0, SPLIT), (W, SPLIT)], fill=BRASS + (230,), width=2)
    d.line([(0, SPLIT + FACE_H), (W, SPLIT + FACE_H)], fill=BRASS + (120,), width=1)
    # rótulos (canto inferior esquerdo do painel do rosto, acima da zona de UI)
    for L in labels:
        u = t - L['t0']
        if 0 <= u <= L['dur']:
            a = min(ease(u / 0.4), ease((L['dur'] - u) / 0.4))
            dy = (1 - ease(u / 0.4)) * 16
            im = L['img']; paste(img, im, 56, 1552 - im.height + dy, a)
    # legenda centrada na emenda, dentro da área segura horizontal (x 60–960)
    for c in caps:
        if c['t0'] - 0.05 <= t < c['end']:
            im = render_cap(c, t)
            a = min(1.0, (t - c['t0'] + 0.05) / 0.12, (c['end'] - t) / 0.10)
            cx = 510; paste(img, im, cx - im.width / 2, SPLIT - im.height / 2, max(0, a))
    enc.stdin.write(np.asarray(img.convert('RGB')).tobytes())
    if f % 300 == 0: print(f'quadro {f}/{nf}', flush=True)
enc.stdin.close(); enc.wait(); scr.kill(); fac.kill()
json.dump({'keeps': keeps, 'dur': DUR, 'anchors_final': AN,
           'captions': [{'t0': c['t0'], 'end': c['end'], 'text': ' / '.join(' '.join(l) for l in c['lines'])} for c in caps]},
          open(os.path.join(WD, 'timeline.json'), 'w'), indent=1, ensure_ascii=False)
final_audio_fix(out)
print('OK', out, f'{nf / FPS:.2f}s')
