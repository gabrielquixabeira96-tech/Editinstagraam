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
const HookTitle = ({lines = [['TÍTULO']], y = 560, stagger = 3, fontSize = 112, durationInFrames}) => {
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
const LowerThird = ({name = 'Dr. Gabriel Quixabeira', sub = 'CRM-MT 14.954', y = 1120, durationInFrames}) => {
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
const ArticleFigure = ({image, highlight = null, caption = '', credit = '', y = 480, opaque = false, durationInFrames}) => {
  useFont();
  const f = useCurrentFrame();
  const inP = enter(f, 0, 10);
  const kb = interpolate(f, [0, durationInFrames], [1.0, 1.08], {easing: inOut});
  const hp = interpolate(f, [14, 30], [0, 1], {easing: inOut, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const W = 950, H = 640;
  return (
    <AbsoluteFill style={{fontFamily: FONT, color: 'white', background: opaque ? '#0B0F14' : 'transparent', opacity: fadeOut(f, durationInFrames)}}>
      <div style={{position: 'absolute', top: y, left: 65, width: W, opacity: inP, transform: `translateY(${(1 - inP) * 24}px)`}}>
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
const BigText = ({text = 'PROTEJA', y = 640, fontSize = 230, color = 'white', durationInFrames}) => {
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
const Label = ({text = 'Recriação ilustrativa (IA)', y = 1180, align = 'left', size = 28, warn = false, durationInFrames}) => {
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

const COMPS = {HookTitle, Counter, BarChart, LineChart, Callout, ProgressBar, LowerThird, ArticleFigure, BigText, Label};
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
