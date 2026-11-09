// Gráficos animados do reel-director — identidade única: Poppins Bold, branco + acento #39E58C,
// caixas rgba(8,12,18,.82), easing sempre, nada de neon/gradiente/emoji/wipe/giro.
// Todas as composições são 1080x1920 @30 fps, fundo TRANSPARENTE (exceto quando `opaque`),
// duração vinda de props.durationInFrames. Coordenadas sempre dentro da área segura
// (x 65–1015, y 470–1250 para destaque; topo < 270 e base > 1250 ficam livres).
import React from 'react';
import {
  registerRoot, Composition, AbsoluteFill, useCurrentFrame, useVideoConfig, spring, interpolate,
  Easing, staticFile, delayRender, continueRender, Img,
} from 'remotion';

const ACCENT = '#39E58C';
const WARN = '#FFB547';
const BOX = 'rgba(8,12,18,.82)';
const FONT = 'PoppinsDir';

// ---------- fonte ----------
let fontHandle = null;
const useFont = () => {
  const [h] = React.useState(() => (fontHandle === null ? delayRender('font') : null));
  React.useEffect(() => {
    if (h === null) return;
    const f = new FontFace(FONT, `url(${staticFile('Poppins-Bold.ttf')})`, {weight: '700'});
    f.load().then((ff) => { document.fonts.add(ff); fontHandle = true; continueRender(h); })
      .catch(() => continueRender(h));
  }, [h]);
};

const ease = Easing.bezier(0.22, 1, 0.36, 1); // easeOutQuint-ish
const inOut = Easing.bezier(0.65, 0, 0.35, 1);
const fadeOut = (f, total, d = 8) => interpolate(f, [total - d, total], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
const enter = (f, start, d = 10) => interpolate(f, [start, start + d], [0, 1], {easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

const Box = ({children, style}) => (
  <div style={{background: BOX, borderRadius: 28, borderLeft: `6px solid ${ACCENT}`, padding: '28px 40px',
    boxShadow: '0 18px 50px rgba(0,0,0,.35)', ...style}}>{children}</div>
);

// ---------- 1. HookTitle: kinetic typography (palavra a palavra, blur→nítido, sobe 40 px) ----------
// props: lines: [["CÂNCER","DE"],["PELE","*NÃO*"]] (palavra entre * = acento), y (topo do bloco), stagger (q)
const HookTitleDir = ({lines = [['TÍTULO']], y = 560, stagger = 3, fontSize = 112, durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  let k = 0;
  const out = fadeOut(f, durationInFrames);
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white'}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', flexDirection: 'column',
        alignItems: 'center', gap: 6, opacity: out}}>
        {lines.map((ln, li) => (
          <div key={li} style={{display: 'flex', gap: 26, justifyContent: 'center', flexWrap: 'wrap'}}>
            {ln.map((w, wi) => {
              const i = k++;
              const p = enter(f, i * stagger, 11);
              const acc = w.startsWith('*');
              const txt = acc ? w.replaceAll('*', '') : w;
              return (
                <span key={wi} style={{fontSize, lineHeight: 1.08, letterSpacing: -1,
                  color: acc ? ACCENT : 'white', opacity: p, filter: `blur(${(1 - p) * 14}px)`,
                  transform: `translateY(${(1 - p) * 40}px) scale(${0.92 + 0.08 * p})`,
                  textShadow: '0 6px 28px rgba(0,0,0,.55), 0 2px 4px rgba(0,0,0,.6)'}}>{txt}</span>
              );
            })}
          </div>
        ))}
        <div style={{height: 8, marginTop: 18, background: ACCENT, borderRadius: 4,
          width: interpolate(f, [k * stagger, k * stagger + 14], [0, 220], {easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}} />
      </div>
    </AbsoluteFill>
  );
};

// ---------- 2. Counter: número que conta + anel que se desenha ----------
// props: from, to, decimals, prefix, suffix, label, ring (0–1 fração do anel), y
const Counter = ({from = 0, to = 100, decimals = 0, prefix = '', suffix = '', label = '', ring = null, y = 600, durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const p = interpolate(f, [4, 40], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const v = from + (to - from) * p;
  const inP = enter(f, 0, 10);
  const R = 190, C = 2 * Math.PI * R;
  const frac = ring === null ? null : ring * p;
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white', opacity: fadeOut(f, durationInFrames)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', flexDirection: 'column', alignItems: 'center',
        transform: `translateY(${(1 - inP) * 24}px)`, opacity: inP}}>
        <Box style={{display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '40px 56px', minWidth: 560}}>
          <div style={{position: 'relative', width: 440, height: frac === null ? 'auto' : 440, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
            {frac !== null && (
              <svg width={440} height={440} style={{position: 'absolute', inset: 0, transform: 'rotate(-90deg)'}}>
                <circle cx={220} cy={220} r={R} stroke="rgba(255,255,255,.14)" strokeWidth={22} fill="none" />
                <circle cx={220} cy={220} r={R} stroke={ACCENT} strokeWidth={22} fill="none" strokeLinecap="round"
                  strokeDasharray={C} strokeDashoffset={C * (1 - frac)} />
              </svg>
            )}
            <div style={{fontSize: 150, lineHeight: 1, color: ACCENT, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap'}}>
              {prefix}{v.toFixed(decimals).replace('.', ',')}<span style={{fontSize: 64, marginLeft: 8}}>{suffix}</span>
            </div>
          </div>
          {label && <div style={{fontSize: 46, marginTop: 22, textAlign: 'center', maxWidth: 820, opacity: enter(f, 12, 10)}}>{label}</div>}
        </Box>
      </div>
    </AbsoluteFill>
  );
};

// ---------- 3. BarChart: barras que crescem em sequência + valores ----------
// props: title, bars:[{label, value, highlight}], unit, y, source
const BarChart = ({title = '', bars = [], unit = '', y = 520, source = '', durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const max = Math.max(...bars.map((b) => b.value), 1);
  const inP = enter(f, 0, 10);
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white', opacity: fadeOut(f, durationInFrames)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, opacity: inP, transform: `translateY(${(1 - inP) * 24}px)`}}>
        <Box style={{padding: '36px 44px'}}>
          {title && <div style={{fontSize: 46, marginBottom: 28}}>{title}</div>}
          {bars.map((b, i) => {
            const p = interpolate(f, [8 + i * 6, 30 + i * 6], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
            return (
              <div key={i} style={{marginBottom: 22}}>
                <div style={{fontSize: 32, opacity: .85, marginBottom: 8}}>{b.label}</div>
                <div style={{display: 'flex', alignItems: 'center', gap: 18}}>
                  <div style={{height: 54, borderRadius: 10, width: `${(b.value / max) * 620 * p}px`,
                    background: b.highlight ? ACCENT : 'rgba(255,255,255,.55)'}} />
                  <div style={{fontSize: 40, color: b.highlight ? ACCENT : 'white', opacity: p}}>
                    {(b.value * p).toFixed(Number.isInteger(b.value) ? 0 : 1).replace('.', ',')}{unit}
                  </div>
                </div>
              </div>
            );
          })}
          {source && <div style={{fontSize: 24, opacity: .6, marginTop: 6}}>Fonte: {source}</div>}
        </Box>
      </div>
    </AbsoluteFill>
  );
};

// ---------- 4. LineChart: curva que se desenha (strokeDashoffset) + ponto final ----------
// props: title, points:[[x,y]...] (0–1), labels:[esq, dir], y, source
const LineChart = ({title = '', points = [[0, .2], [.5, .5], [1, .9]], labels = [], y = 540, source = '', durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const W = 820, H = 420;
  const d = points.map(([px, py], i) => `${i ? 'L' : 'M'}${px * W},${H - py * H}`).join(' ');
  const p = interpolate(f, [6, 46], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const inP = enter(f, 0, 10);
  const last = points[points.length - 1];
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white', opacity: fadeOut(f, durationInFrames)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, opacity: inP}}>
        <Box style={{padding: '36px 44px'}}>
          {title && <div style={{fontSize: 44, marginBottom: 20}}>{title}</div>}
          <svg width={W} height={H + 10} style={{overflow: 'visible'}}>
            {[0.25, .5, .75, 1].map((g) => <line key={g} x1={0} x2={W} y1={H - g * H} y2={H - g * H} stroke="rgba(255,255,255,.1)" strokeWidth={2} />)}
            <path d={d} fill="none" stroke={ACCENT} strokeWidth={10} strokeLinecap="round" strokeLinejoin="round"
              pathLength={1} strokeDasharray={1} strokeDashoffset={1 - p} />
            <circle cx={last[0] * W} cy={H - last[1] * H} r={18 * enter(f, 44, 8)} fill={ACCENT} />
          </svg>
          {labels.length > 0 && <div style={{display: 'flex', justifyContent: 'space-between', fontSize: 28, opacity: .7, marginTop: 12}}>
            {labels.map((l, i) => <span key={i}>{l}</span>)}</div>}
          {source && <div style={{fontSize: 24, opacity: .6, marginTop: 10}}>Fonte: {source}</div>}
        </Box>
      </div>
    </AbsoluteFill>
  );
};

// ---------- 5. Callout: ponto pulsa, linha se desenha até o rótulo ----------
// props: target:[x,y] px, label, side ('left'|'right'), labelY
const Callout = ({target = [540, 900], label = 'Rótulo', side = 'left', labelY = 560, durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const [tx, ty] = target;
  const lx = side === 'left' ? 110 : 600;
  const elbowX = side === 'left' ? 470 : 610;
  const dot = enter(f, 0, 8);
  const line = interpolate(f, [6, 20], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const lab = enter(f, 18, 10);
  const pulse = 1 + 0.35 * Math.max(0, Math.sin(f / 5));
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white', opacity: fadeOut(f, durationInFrames)}}>
      <svg width={1080} height={1920} style={{position: 'absolute'}}>
        <circle cx={tx} cy={ty} r={30 * pulse * dot} fill="none" stroke={ACCENT} strokeWidth={4} opacity={0.6} />
        <circle cx={tx} cy={ty} r={13 * dot} fill={ACCENT} />
        <path d={`M${tx},${ty} L${elbowX},${labelY + 46} L${side === 'left' ? 470 : 610},${labelY + 46}`} fill="none"
          stroke={ACCENT} strokeWidth={5} pathLength={1} strokeDasharray={1} strokeDashoffset={1 - line} />
      </svg>
      <div style={{position: 'absolute', top: labelY, left: lx, width: 370, opacity: lab, transform: `translateY(${(1 - lab) * 24}px)`}}>
        <Box style={{padding: '18px 26px', fontSize: 38, lineHeight: 1.15}}>{label}</Box>
      </div>
    </AbsoluteFill>
  );
};

// ---------- 6. ProgressBar + capítulo (barra fina logo abaixo da faixa do topo) ----------
// props: chapters:[{t0,t1,label}] em quadros relativos ao vídeo; total (quadros)
const ProgressBar = ({chapters = [], durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const total = durationInFrames;
  const cur = chapters.find((c) => f >= c.t0 && f < c.t1);
  const appear = enter(f, 0, 10);
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white'}}>
      <div style={{position: 'absolute', top: 290, left: 65, width: 950, opacity: appear * 0.95}}>
        <div style={{height: 8, background: 'rgba(255,255,255,.22)', borderRadius: 4, overflow: 'hidden', display: 'flex'}}>
          <div style={{width: `${(f / total) * 100}%`, background: ACCENT}} />
        </div>
        {chapters.map((c, i) => i > 0 && <div key={i} style={{position: 'absolute', top: -3, left: `${(c.t0 / total) * 100}%`, width: 4, height: 14, background: 'white', borderRadius: 2}} />)}
        {cur && (() => { const p = enter(f, cur.t0, 8); return (
          <div style={{marginTop: 14, fontSize: 30, opacity: p, transform: `translateY(${(1 - p) * 10}px)`, letterSpacing: 1,
            textShadow: '0 2px 10px rgba(0,0,0,.7)'}}>{cur.label}</div>); })()}
      </div>
    </AbsoluteFill>
  );
};

// ---------- 7. LowerThird: barra 6 px + deslize 24 px (nome + CRM) ----------
const LowerThirdDir = ({name = 'Dr. Gabriel Quixabeira', sub = 'CRM-MT 14.954', y = 1120, durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const p = enter(f, 0, 11);
  const p2 = enter(f, 5, 11);
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white', opacity: fadeOut(f, durationInFrames)}}>
      <div style={{position: 'absolute', top: y, left: 65, display: 'flex', alignItems: 'stretch', opacity: p, transform: `translateX(${(1 - p) * -24}px)`}}>
        <div style={{width: 6, background: ACCENT, borderRadius: 3}} />
        <div style={{background: BOX, padding: '16px 28px', borderRadius: '0 18px 18px 0'}}>
          <div style={{fontSize: 40}}>{name}</div>
          <div style={{fontSize: 28, opacity: .8 * p2, color: ACCENT}}>{sub}</div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------- 8. ArticleFigure: figura real de artigo, Ken Burns + marca-texto animado + crédito ----------
// props: image (arquivo em public/), highlight:{x,y,w,h} fração da imagem, caption, credit, y
const ArticleFigureDir = ({image, highlight = null, caption = '', credit = '', y = 480, opaque = false, w = 950, h = 640, durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const inP = enter(f, 0, 10);
  const kb = interpolate(f, [0, durationInFrames], [1.0, 1.08], {easing: inOut});
  const hp = interpolate(f, [14, 30], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const W = w, H = h;  // w/h opcionais: caixa menor e centralizada para a sombra caber na área segura (x 65–1015)
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white', background: opaque ? '#0B0F14' : 'transparent', opacity: fadeOut(f, durationInFrames)}}>
      <div style={{position: 'absolute', top: y, left: Math.round((1080 - W) / 2), width: W, opacity: inP, transform: `translateY(${(1 - inP) * 24}px)`}}>
        <div style={{width: W, height: H, overflow: 'hidden', borderRadius: 24, background: '#fff', boxShadow: '0 24px 60px rgba(0,0,0,.45)', position: 'relative'}}>
          <div style={{position: 'absolute', inset: 0, transform: `scale(${kb})`, transformOrigin: highlight ? `${(highlight.x + highlight.w / 2) * 100}% ${(highlight.y + highlight.h / 2) * 100}%` : '50% 50%'}}>
            {image && <Img src={staticFile(image)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />}
            {highlight && (
              <div style={{position: 'absolute', left: `${highlight.x * 100}%`, top: `${highlight.y * 100}%`, height: `${highlight.h * 100}%`,
                width: `${highlight.w * 100 * hp}%`, background: 'rgba(57,229,140,.28)', borderBottom: `6px solid ${ACCENT}`, borderRadius: 6}} />
            )}
          </div>
        </div>
        {caption && <Box style={{marginTop: 18, fontSize: 36, padding: '18px 28px', opacity: enter(f, 20, 10)}}>{caption}</Box>}
        {credit && <div style={{marginTop: 10, fontSize: 22, opacity: .75, textShadow: '0 1px 6px rgba(0,0,0,.8)'}}>{credit}</div>}
      </div>
    </AbsoluteFill>
  );
};

// ---------- 9. BigText: texto gigante (para "texto atrás da pessoa"; o ffmpeg recoloca a pessoa por cima) ----------
const BigTextDir = ({text = 'PROTEJA', y = 640, fontSize = 230, color = 'white', durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const p = enter(f, 0, 14);
  return (
    <AbsoluteFill style={{fontFamily: FONT, opacity: fadeOut(f, durationInFrames)}}>
      <div style={{position: 'absolute', top: y, width: 1080, textAlign: 'center', fontSize, lineHeight: 1, letterSpacing: -4,
        color, opacity: p, transform: `translateY(${(1 - p) * 60}px) scale(${1.1 - 0.1 * p})`}}>{text}</div>
    </AbsoluteFill>
  );
};

// ---------- 10. Label: rótulo pequeno (IA, disclaimer, CTA de envio) ----------
const LabelDir = ({text = 'Recriação ilustrativa (IA)', y = 1180, align = 'left', size = 28, warn = false, durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const p = enter(f, 0, 9);
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white', opacity: fadeOut(f, durationInFrames)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', justifyContent: align === 'center' ? 'center' : 'flex-start'}}>
        <div style={{background: BOX, borderRadius: 14, padding: '10px 20px', fontSize: size, opacity: p,
          borderLeft: `4px solid ${warn ? WARN : ACCENT}`, transform: `translateY(${(1 - p) * 12}px)`}}>{text}</div>
      </div>
    </AbsoluteFill>
  );
};

// ======================================================================================================
// Estilo "classico" (props.look === 'classico'; ativado por styles/classico.json → 04_graphics injeta look):
// conteúdo moderno, estética clássica. Serifa Cormorant Garamond em caixa alta espaçada, branco × preto/grafite,
// caixas QUADRADAS translúcidas, filetes de 1–2 px, sem verde, sem blur/escala elástica: só fade + leve deslocamento.
// ======================================================================================================
const SERIF = 'CormorantDir';
let serifHandle = null;
const useSerif = () => {
  const [h] = React.useState(() => (serifHandle === null ? delayRender('serif') : null));
  React.useEffect(() => {
    if (h === null) return;
    const faces = [
      new FontFace(SERIF, `url(${staticFile('CormorantGaramond-SemiBold.ttf')})`, {weight: '600'}),
      new FontFace(SERIF, `url(${staticFile('CormorantGaramond-Medium.ttf')})`, {weight: '500'}),
    ];
    Promise.all(faces.map((ff) => ff.load())).then((ls) => { ls.forEach((ff) => document.fonts.add(ff)); serifHandle = true; continueRender(h); })
      .catch(() => continueRender(h));
  }, [h]);
};
const C_BOX = 'rgba(12,12,12,.88)';
const C_SOFT = 'rgba(255,255,255,.80)';
const C_SHADOW = '0 2px 14px rgba(0,0,0,.65), 0 1px 2px rgba(0,0,0,.6)';
const soft = (f, start, d = 12) => interpolate(f, [start, start + d], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
const Rule = ({w, opacity = 1, style}) => <div style={{height: 1.5, width: w, background: 'rgba(255,255,255,.9)', opacity, ...style}} />;

const HookTitleClassico = ({lines = [['TÍTULO']], y = 560, stagger = 3, fontSize = 92, box = false, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  let k = 0;
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: 'white'}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', flexDirection: 'column',
        alignItems: 'center', gap: 4, opacity: fadeOut(f, durationInFrames, 10)}}>
        <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4,
          ...(box ? {background: C_BOX, padding: '26px 40px 22px', opacity: soft(f, 0, 8)} : {})}}>
        {lines.map((ln, li) => (
          <div key={li} style={{display: 'flex', gap: Math.round(fontSize * 0.34), justifyContent: 'center', flexWrap: 'wrap'}}>
            {ln.map((w, wi) => {
              const i = k++;
              const p = soft(f, i * stagger, 12);
              const acc = w.startsWith('*');
              const txt = acc ? w.replaceAll('*', '') : w;
              return (
                <span key={wi} style={{fontSize, fontWeight: 600, lineHeight: 1.1, letterSpacing: '0.09em', textTransform: 'uppercase',
                  color: acc ? '#FFFFFF' : C_SOFT, opacity: p, transform: `translateY(${(1 - p) * 14}px)`, textShadow: C_SHADOW,
                  borderBottom: acc ? `1.5px solid rgba(255,255,255,${0.9 * soft(f, k * stagger + 6, 12)})` : 'none', paddingBottom: acc ? 2 : 0}}>{txt}</span>
              );
            })}
          </div>
        ))}
        <Rule w={interpolate(f, [k * stagger, k * stagger + 16], [0, 140], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}
          style={{marginTop: 16, boxShadow: '0 1px 6px rgba(0,0,0,.5)'}} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

const BigTextClassico = ({text = 'PROJEÇÃO', y = 640, fontSize = 150, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = soft(f, 0, 16);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, width: 1080, textAlign: 'center', fontSize, fontWeight: 600, lineHeight: 1,
        letterSpacing: '0.12em', textTransform: 'uppercase', color: 'white', opacity: p, textShadow: C_SHADOW,
        transform: `translateY(${(1 - p) * 18}px)`}}>{text}</div>
    </AbsoluteFill>
  );
};

const LowerThirdClassico = ({name = 'Dr. Gabriel Quixabeira', sub = 'CRM-MT 14.954', y = 1120, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = soft(f, 0, 12);
  const r = soft(f, 4, 16);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: 'white', opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, opacity: p, transform: `translateY(${(1 - p) * 10}px)`}}>
        <div style={{background: C_BOX, padding: '16px 26px 14px'}}>
          <div style={{fontSize: 38, fontWeight: 600, letterSpacing: '0.06em'}}>{name}</div>
          <Rule w={260 * r} opacity={0.7} style={{margin: '8px 0 8px'}} />
          <div style={{fontSize: 24, fontWeight: 500, letterSpacing: '0.22em', textTransform: 'uppercase', color: C_SOFT}}>{sub}</div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const ArticleFigureClassico = ({image, highlight = null, caption = '', credit = '', y = 480, w = 950, h = 640, capSize = 38, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const inP = soft(f, 0, 12);
  const kb = interpolate(f, [0, durationInFrames], [1.0, 1.06], {easing: inOut});
  const hp = interpolate(f, [14, 30], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: 'white', opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: Math.round((1080 - w) / 2), width: w, opacity: inP, transform: `translateY(${(1 - inP) * 12}px)`}}>
        <div style={{width: w, height: h, overflow: 'hidden', background: '#fff', position: 'relative',
          outline: '1.5px solid rgba(255,255,255,.9)', outlineOffset: 6, boxShadow: '0 8px 26px rgba(0,0,0,.40)'}}>
          <div style={{position: 'absolute', inset: 0, transform: `scale(${kb})`, transformOrigin: highlight ? `${(highlight.x + highlight.w / 2) * 100}% ${(highlight.y + highlight.h / 2) * 100}%` : '50% 50%'}}>
            {image && <Img src={staticFile(image)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />}
            {highlight && (
              <div style={{position: 'absolute', left: `${highlight.x * 100}%`, top: `${highlight.y * 100}%`, height: `${highlight.h * 100}%`,
                width: `${highlight.w * 100}%`, border: '2px solid rgba(20,20,20,.9)', opacity: hp}} />
            )}
          </div>
        </div>
        {caption && (
          <div style={{marginTop: 22, background: C_BOX, padding: '12px 22px', fontSize: capSize, fontWeight: 600, letterSpacing: '0.02em',
            lineHeight: 1.18, opacity: soft(f, 10, 12)}}>{caption}</div>
        )}
        {credit && <div style={{marginTop: 8, display: 'inline-block', background: 'rgba(12,12,12,.85)', padding: '5px 12px', fontSize: 23, fontWeight: 600,
          letterSpacing: '0.04em', color: '#E6E6E6', opacity: soft(f, 14, 12)}}>{credit}</div>}
      </div>
    </AbsoluteFill>
  );
};

const LabelClassico = ({text = 'Recriação ilustrativa (IA)', y = 1180, align = 'left', size = 28, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = soft(f, 0, 10);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: 'white', opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', justifyContent: align === 'center' ? 'center' : 'flex-start'}}>
        <div style={{background: C_BOX, padding: '10px 20px', fontSize: size + 2, fontWeight: 600, letterSpacing: '0.03em', opacity: p,
          transform: `translateY(${(1 - p) * 8}px)`}}>{text}</div>
      </div>
    </AbsoluteFill>
  );
};


// ---------- Classico: Counter / BarChart / Checklist (2026-11-05) — caixa quadrada grafite, branco × cinza, sem acento ----------
const SANS_C = 'PoppinsDir';
const CounterClassico = ({from = 0, to = 100, decimals = 0, prefix = '', suffix = '', label = '', kicker = '', y = 560, numSize = 150, pad = 30, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = interpolate(f, [4, 34], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const v = from + (to - from) * p;
  const inP = soft(f, 0, 12);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: 'white', opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', justifyContent: 'center',
        opacity: inP, transform: `translateY(${(1 - inP) * 12}px)`}}>
        <div style={{background: C_BOX, padding: `${pad}px 54px ${pad}px`, display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 560}}>
          {kicker && <div style={{fontSize: 26, fontWeight: 600, letterSpacing: '0.24em', textTransform: 'uppercase', color: C_SOFT, marginBottom: 10}}>{kicker}</div>}
          <div style={{fontSize: numSize, fontWeight: 600, lineHeight: 1, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', textShadow: C_SHADOW}}>
            {prefix}{v.toFixed(decimals).replace('.', ',')}<span style={{fontSize: Math.round(numSize * 0.47), marginLeft: 10}}>{suffix}</span>
          </div>
          <Rule w={interpolate(f, [10, 30], [0, 200], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})} opacity={0.8} style={{margin: '16px 0 12px'}} />
          {label && <div style={{fontSize: Math.max(30, Math.round(numSize * 0.3)), fontWeight: 600, letterSpacing: '0.04em', textAlign: 'center', maxWidth: 820, opacity: soft(f, 12, 12)}}>{label}</div>}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const BarChartClassico = ({title = '', bars = [], unit = '', y = 520, source = '', durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const max = Math.max(...bars.map((b) => b.value), 1);
  const inP = soft(f, 0, 12);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: 'white', opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, opacity: inP, transform: `translateY(${(1 - inP) * 12}px)`}}>
        <div style={{background: C_BOX, padding: '30px 40px 24px'}}>
          {title && <div style={{fontSize: 34, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6}}>{title}</div>}
          {title && <Rule w={interpolate(f, [4, 20], [0, 180], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})} opacity={0.7} style={{marginBottom: 24}} />}
          {bars.map((b, i) => {
            const pb = interpolate(f, [8 + i * 6, 30 + i * 6], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
            return (
              <div key={i} style={{marginBottom: 18}}>
                <div style={{fontSize: 34, fontWeight: 600, color: b.highlight ? '#FFFFFF' : C_SOFT, marginBottom: 6}}>{b.label}</div>
                <div style={{display: 'flex', alignItems: 'center', gap: 18}}>
                  <div style={{height: 26, width: `${(b.value / max) * 600 * pb}px`, background: b.highlight ? 'rgba(255,255,255,.95)' : 'rgba(255,255,255,.45)'}} />
                  <div style={{fontSize: 40, fontWeight: 600, fontVariantNumeric: 'tabular-nums', opacity: pb}}>
                    {Math.round(b.value * pb)}{unit}
                  </div>
                </div>
              </div>
            );
          })}
          {source && <div style={{fontSize: 22, fontWeight: 600, letterSpacing: '0.03em', color: '#D0D0D0', marginTop: 10}}>{source}</div>}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const ChecklistClassico = ({title = '', items = [], y = 520, stagger = 9, instant = false, footer = '', durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  // instant: continuação de um card anterior (sem animação de entrada do quadro/título)
  const inP = instant ? 1 : soft(f, 0, 12);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: 'white', opacity: items.length && items[0].keepOut ? 1 : fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, opacity: inP, transform: `translateY(${(1 - inP) * 12}px)`}}>
        <div style={{background: C_BOX, padding: '22px 36px 14px'}}>
          {title && <div style={{fontSize: 30, fontWeight: 600, letterSpacing: '0.2em', textTransform: 'uppercase', color: C_SOFT}}>{title}</div>}
          {title && <Rule w={160 * (instant ? 1 : soft(f, 4, 14))} opacity={0.7} style={{margin: '10px 0 16px'}} />}
          {items.map((it, i) => {
            const st0 = it.at !== undefined ? Math.round(it.at * 30) : 6 + i * stagger;
            const pi = it.shown ? 1 : soft(f, st0, 12);
            const strike = it.strike ? (it.struck ? 1 : soft(f, it.strikeAt !== undefined ? Math.round(it.strikeAt * 30) : st0 + 14, 10)) : 0;
            return (
              <div key={i} style={{display: 'flex', alignItems: 'baseline', gap: 18, marginBottom: 10, opacity: pi, transform: `translateX(${(1 - pi) * 14}px)`}}>
                <div style={{width: 14, height: 1.5, background: 'rgba(255,255,255,.9)', transform: 'translateY(-10px)'}} />
                <div style={{position: 'relative', fontSize: it.size || 46, fontWeight: 600, letterSpacing: '0.02em', color: it.dim ? C_SOFT : '#FFFFFF'}}>
                  {it.text}
                  {it.strike && <div style={{position: 'absolute', left: 0, top: '56%', height: 2, width: `${strike * 100}%`, background: 'rgba(255,255,255,.95)'}} />}
                </div>
                {it.note && <div style={{fontSize: 26, fontWeight: 500, letterSpacing: '0.12em', textTransform: 'uppercase', color: C_SOFT}}>{it.note}</div>}
              </div>
            );
          })}
          {footer && <div style={{fontSize: 24, fontWeight: 600, letterSpacing: '0.03em', color: '#D0D0D0', marginTop: 8, opacity: instant ? 1 : soft(f, 10, 12)}}>{footer}</div>}
        </div>
      </div>
    </AbsoluteFill>
  );
};


// ======================================================================================================
// Estilo "grand_hotel" (props.look === 'grand_hotel'; styles/grand_hotel.json, 2026-11-09; skill estetica-grand-hotel):
// luxo silencioso europeu à meia-luz. Serifa Cormorant Garamond; rótulos em VERSALETE com letter-spacing 0.24em;
// branco-gelo #F4F1EA; destaque âmbar #E2B66E; filetes de latão #B08D57; caixas de cantos RETOS em carvão translúcido.
// Animação: só fade + deslocamento vertical curto (inOut). Nada de escala, bounce, pop, verde, gradiente, emoji.
// ======================================================================================================
const GH = {ink: '#F4F1EA', ivory: '#EDE6D6', amber: '#E2B66E', brass: '#B08D57', box: 'rgba(14,13,11,.80)',
  soft: 'rgba(237,230,214,.78)', shadow: '0 2px 18px rgba(14,13,11,.75), 0 1px 3px rgba(14,13,11,.7)'};
const BrassRule = ({w, opacity = 1, style}) => <div style={{height: 1.5, width: w, background: GH.brass, opacity, ...style}} />;
const ruleIn = (f, a, b, w) => interpolate(f, [a, b], [0, w], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

// Gancho: kicker opcional em versalete + título em serifa (palavra com * = destaque âmbar sublinhado por filete de latão).
const HookTitleGH = ({lines = [['TÍTULO']], kicker = '', y = 560, stagger = 3, fontSize = 96, box = true, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  let k = 0;
  const nw = lines.reduce((a, l) => a + l.length, 0);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: GH.ink}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', flexDirection: 'column', alignItems: 'center',
        opacity: fadeOut(f, durationInFrames, 10)}}>
        <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
          ...(box ? {background: GH.box, padding: '28px 46px 26px', borderTop: `1.5px solid ${GH.brass}`, borderBottom: `1.5px solid ${GH.brass}`,
            opacity: soft(f, 0, 8)} : {})}}>
          {kicker && <div style={{fontSize: 26, fontWeight: 500, letterSpacing: '0.26em', textTransform: 'uppercase', color: GH.brass,
            marginBottom: 10, opacity: soft(f, 0, 10)}}>{kicker}</div>}
          {lines.map((ln, li) => (
            <div key={li} style={{display: 'flex', gap: Math.round(fontSize * 0.26), justifyContent: 'center', flexWrap: 'wrap'}}>
              {ln.map((w, wi) => {
                const i = k++;
                const p = soft(f, 2 + i * stagger, 12);
                const acc = w.startsWith('*');
                const txt = acc ? w.replaceAll('*', '') : w;
                return (
                  <span key={wi} style={{fontSize, fontWeight: 600, lineHeight: 1.08, letterSpacing: '0.02em',
                    color: acc ? GH.amber : GH.ink, opacity: p, transform: `translateY(${(1 - p) * 12}px)`, textShadow: GH.shadow,
                    borderBottom: acc ? `1.5px solid ${GH.brass}` : 'none', paddingBottom: acc ? 2 : 0}}>{txt}</span>
                );
              })}
            </div>
          ))}
          <BrassRule w={ruleIn(f, nw * stagger, nw * stagger + 16, 120)} style={{marginTop: 14}} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// Frase-chave / mito: texto em serifa sobre caixa carvão, rótulo opcional em versalete latão.
const KeyphraseGH = ({text = '', kicker = '', y = 560, fontSize = 64, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = soft(f, 0, 12);
  const parts = String(text).split(/(\*[^*]+\*)/g).filter(Boolean);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: GH.ink, opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', justifyContent: 'center',
        opacity: p, transform: `translateY(${(1 - p) * 12}px)`}}>
        <div style={{background: GH.box, padding: '24px 42px 22px', maxWidth: 900, textAlign: 'center', borderLeft: `1.5px solid ${GH.brass}`}}>
          {kicker && <div style={{fontSize: 24, fontWeight: 500, letterSpacing: '0.26em', textTransform: 'uppercase', color: GH.brass, marginBottom: 8}}>{kicker}</div>}
          <div style={{fontSize, fontWeight: 600, lineHeight: 1.14, textShadow: GH.shadow}}>
            {parts.map((t, i) => t.startsWith('*') ? <span key={i} style={{color: GH.amber}}>{t.replaceAll('*', '')}</span> : <span key={i}>{t}</span>)}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const LowerThirdGH = ({name = 'Dr. Gabriel Quixabeira', sub = 'CRM-MT 14.954', role = '', y = 1000, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = soft(f, 0, 12);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: GH.ink, opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, opacity: p, transform: `translateY(${(1 - p) * 10}px)`}}>
        <div style={{background: GH.box, padding: '16px 28px 14px', borderLeft: `1.5px solid ${GH.brass}`}}>
          <div style={{fontSize: 40, fontWeight: 600, letterSpacing: '0.03em'}}>{name}</div>
          <BrassRule w={ruleIn(f, 4, 20, 240)} style={{margin: '8px 0 9px'}} />
          <div style={{fontSize: 22, fontWeight: 500, letterSpacing: '0.24em', textTransform: 'uppercase', color: GH.soft}}>
            {sub}{role ? <span style={{color: GH.brass}}>{'  ·  '}</span> : null}{role}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// Disclaimer / rótulo IA / CTA: versalete pequeno, carvão translúcido, filete de latão no topo.
const LabelGH = ({text = 'Recriação ilustrativa (IA)', y = 1180, align = 'center', size = 24, caps = true, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = soft(f, 0, 10);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: GH.ink, opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', justifyContent: align === 'center' ? 'center' : 'flex-start'}}>
        <div style={{background: GH.box, padding: '11px 22px 10px', borderTop: `1.5px solid ${GH.brass}`, fontSize: size, fontWeight: 600,
          letterSpacing: caps ? '0.2em' : '0.03em', textTransform: caps ? 'uppercase' : 'none', opacity: p, transform: `translateY(${(1 - p) * 8}px)`}}>{text}</div>
      </div>
    </AbsoluteFill>
  );
};

const CounterGH = ({from = 0, to = 100, decimals = 0, prefix = '', suffix = '', label = '', kicker = '', y = 520, numSize = 150, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = interpolate(f, [4, 34], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const v = from + (to - from) * p;
  const inP = soft(f, 0, 12);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: GH.ink, opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, display: 'flex', justifyContent: 'center', opacity: inP, transform: `translateY(${(1 - inP) * 12}px)`}}>
        <div style={{background: GH.box, padding: '28px 54px', display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 560,
          borderTop: `1.5px solid ${GH.brass}`, borderBottom: `1.5px solid ${GH.brass}`}}>
          {kicker && <div style={{fontSize: 24, fontWeight: 500, letterSpacing: '0.26em', textTransform: 'uppercase', color: GH.brass, marginBottom: 10}}>{kicker}</div>}
          <div style={{fontSize: numSize, fontWeight: 600, lineHeight: 1, color: GH.amber, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', textShadow: GH.shadow}}>
            {prefix}{v.toFixed(decimals).replace('.', ',')}<span style={{fontSize: Math.round(numSize * 0.45), marginLeft: 10}}>{suffix}</span>
          </div>
          <BrassRule w={ruleIn(f, 10, 30, 180)} style={{margin: '16px 0 12px'}} />
          {label && <div style={{fontSize: 40, fontWeight: 600, textAlign: 'center', maxWidth: 820, opacity: soft(f, 12, 12)}}>{label}</div>}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const ChecklistGH = ({title = '', items = [], y = 520, stagger = 9, footer = '', durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const inP = soft(f, 0, 12);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, color: GH.ink, opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: 950, opacity: inP, transform: `translateY(${(1 - inP) * 12}px)`}}>
        <div style={{background: GH.box, padding: '22px 38px 16px', borderLeft: `1.5px solid ${GH.brass}`}}>
          {title && <div style={{fontSize: 26, fontWeight: 500, letterSpacing: '0.26em', textTransform: 'uppercase', color: GH.brass}}>{title}</div>}
          {title && <BrassRule w={160 * soft(f, 4, 14)} style={{margin: '10px 0 16px'}} />}
          {items.map((it, i) => {
            const st0 = it.at !== undefined ? Math.round(it.at * 30) : 6 + i * stagger;
            const pi = soft(f, st0, 12);
            return (
              <div key={i} style={{display: 'flex', alignItems: 'baseline', gap: 18, marginBottom: 10, opacity: pi, transform: `translateY(${(1 - pi) * 8}px)`}}>
                <div style={{width: 14, height: 1.5, background: GH.brass, transform: 'translateY(-10px)'}} />
                <div style={{fontSize: it.size || 48, fontWeight: 600, color: it.hi ? GH.amber : GH.ink}}>{it.text}</div>
              </div>
            );
          })}
          {footer && <div style={{fontSize: 22, letterSpacing: '0.04em', color: GH.soft, marginTop: 8}}>{footer}</div>}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const BigTextGH = ({text = 'TEMA', y = 640, fontSize = 150, durationInFrames}) => {
  useSerif();
  const f = useCurrentFrame();
  const p = soft(f, 0, 16);
  return (
    <AbsoluteFill style={{fontFamily: SERIF, opacity: fadeOut(f, durationInFrames, 10)}}>
      <div style={{position: 'absolute', top: y, width: 1080, textAlign: 'center', fontSize, fontWeight: 600, lineHeight: 1,
        letterSpacing: '0.14em', textTransform: 'uppercase', color: GH.ivory, opacity: p, textShadow: GH.shadow,
        transform: `translateY(${(1 - p) * 16}px)`}}>{text}</div>
    </AbsoluteFill>
  );
};

const byLook = (Dir, Cls, Gh) => (props) => (props.look === 'grand_hotel' && Gh ? <Gh {...props} /> : props.look === 'classico' ? <Cls {...props} /> : <Dir {...props} />);
const HookTitle = byLook(HookTitleDir, HookTitleClassico, HookTitleGH);
const LowerThird = byLook(LowerThirdDir, LowerThirdClassico, LowerThirdGH);
const ArticleFigure = byLook(ArticleFigureDir, ArticleFigureClassico);
const BigText = byLook(BigTextDir, BigTextClassico, BigTextGH);
const Label = byLook(LabelDir, LabelClassico, LabelGH);

const CounterC = byLook(Counter, CounterClassico, CounterGH);
const BarChartC = byLook(BarChart, BarChartClassico);
const ChecklistC = byLook(ChecklistClassico, ChecklistClassico, ChecklistGH);
const Keyphrase = byLook(KeyphraseGH, KeyphraseGH, KeyphraseGH);
const COMPS = {HookTitle, Counter: CounterC, BarChart: BarChartC, Checklist: ChecklistC, Keyphrase, LineChart, Callout, ProgressBar, LowerThird, ArticleFigure, BigText, Label};
const calc = ({props}) => ({durationInFrames: Math.max(1, Math.round(props.durationInFrames || 60))});

const Root = () => (
  <>
    {Object.entries(COMPS).map(([id, C]) => (
      <Composition key={id} id={id} component={C} fps={30} width={1080} height={1920}
        durationInFrames={60} defaultProps={{durationInFrames: 60}} calculateMetadata={calc} />
    ))}
  </>
);
registerRoot(Root);
