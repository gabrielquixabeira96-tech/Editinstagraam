"""Trilha de piano sintetizada (obra original, CC0): arpejos lentos em ré menor, 60 bpm, com reverb.
Uso: python3 synth_piano.py saida.wav duracao_s"""
import sys, numpy as np, wave
SR=48000; out=sys.argv[1]; D=float(sys.argv[2])
rng=np.random.default_rng(11)
def hz(m): return 440*2**((m-69)/12)
def note(m, dur, vel):
    n=int(SR*dur); t=np.arange(n)/SR; f=hz(m); y=np.zeros(n)
    for k,a in enumerate([1,.45,.22,.12,.06,.03],1):
        tau=2.2/(1+0.6*(k-1))*(1.3 if m<55 else 1)
        inh=1+0.0004*k*k
        y+=a*np.sin(2*np.pi*f*k*inh*t+rng.uniform(0,6))*np.exp(-t/tau)
    att=np.minimum(1,t/0.006); y*=att*vel
    # martelo de feltro: leve ruído filtrado no ataque
    return y
chords=[(50,[62,65,69,72,76]),(46,[62,65,69,70,74]),(43,[58,62,65,67,70]),(45,[61,64,67,69,73]),
        (50,[62,65,69,72,77]),(41,[60,65,69,72,76]),(43,[58,62,67,70,74]),(45,[57,61,64,67,69])]
N=int(SR*(D+6)); L=np.zeros(N); R=np.zeros(N)
beat=1.0; t0=0.4; i=0
while t0<D:
    bass,up=chords[i%len(chords)]
    seq=[(0,bass,.55),(0,bass+12,.25)]+[(0.5+j*0.75,up[j],.30-0.03*j) for j in range(len(up))]
    for off,m,v in seq:
        s=int(SR*(t0+off)); y=note(m,6.0,v)
        e=min(N,s+len(y)); pan=0.5+0.35*np.tanh((m-62)/12)
        L[s:e]+=y[:e-s]*(1-pan)*1.4; R[s:e]+=y[:e-s]*pan*1.4
    t0+=4.0*beat; i+=1
# reverb: IR de ruído com decaimento exponencial (sala grande, escura)
ir_n=int(SR*3.2); tt=np.arange(ir_n)/SR
def ir(seed):
    r=np.random.default_rng(seed).standard_normal(ir_n)*np.exp(-tt/0.9)
    # escurece: média móvel
    k=np.ones(24)/24; return np.convolve(r,k,'same')*0.02
from numpy.fft import rfft, irfft
def conv(x,h):
    n=1<<int(np.ceil(np.log2(len(x)+len(h)))); return irfft(rfft(x,n)*rfft(h,n),n)[:len(x)]
Lw=conv(L,ir(1)); Rw=conv(R,ir(2))
L=0.75*L+Lw; R=0.75*R+Rw
# passa-baixa suave (1 polo ~3.5 kHz)
a=np.exp(-2*np.pi*3500/SR)
def lp(x):
    from scipy.signal import lfilter
    return lfilter([1-a],[1,-a],x)
try: L=lp(L); R=lp(R)
except Exception: pass
n=int(SR*D); L=L[:n]; R=R[:n]
fade=np.ones(n); fi=int(SR*1.5); fo=int(SR*2.5); fade[:fi]=np.linspace(0,1,fi); fade[-fo:]=np.linspace(1,0,fo)
st=np.stack([L*fade,R*fade],1); st/=np.abs(st).max()/0.7
w=wave.open(out,'wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
w.writeframes((st*32767).astype(np.int16).tobytes()); w.close(); print('ok',out,D)
