#!/usr/bin/env python3
"""KaisTrade animated Reel engine v3 (1080x1920, 30fps, original synthesized music + sfx).
Usage: python3 reel3.py spec.json out.mp4          (render)
       python3 reel3.py spec.json out.mp4 still 1.0 6.5   (write still_<t>.png frames only, for checking)
Spec JSON: {"seed":7,"bpm":100,"scenes":[ ... ]}  (a branded end card is appended automatically)
Scene types:
 {"type":"hook","pre":"Think a tight","big":"STOP LOSS","post":"means zero risk?","post_color":"red"}   # 3.0s. big <= ~10 chars per line, keep short
 {"type":"chart","title1":"Price can gap through","title2":"your stop.","trend":"up|down|range",
   "levels":[{"label":"ENTRY","color":"green","delta":0.3},{"label":"STOP","color":"red","delta":-0.9}],   # delta = price units relative to last close (a candle ~ 1 unit)
   "event":"gap_down|gap_up|none","event_label":"SLIPPAGE","line1":"Your exit fills lower than your stop.","line2":"It happens in fast markets."}   # 5.6s
 {"type":"stamp","stamp":"FACT","color":"green|red|yellow","line1":"A stop limits your loss.","line2":"It doesn't remove risk.","line2_color":"green","foot":"Know the risk. Plan around it."}   # 3.8s
 {"type":"steps","title":"Do this instead","items":["Know where you are wrong","Size the trade to that level","Risk 1% or less per trade"],"foot":"Control the risk. Not the outcome."}   # 4.2s (2-3 items)
Colors: white gray green red yellow. Text max ~28 chars per line at 74px (long lines shrink automatically)."""
import numpy as np, wave, subprocess, os, sys, math, json, tempfile
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from scipy import signal
F='/usr/share/fonts/opentype/inter/Inter-'
def f(w,s): return ImageFont.truetype(F+w+'.otf',int(s))
W,H,FPS=1080,1920,30
BG=(11,17,26);GR=(52,199,123);RD=(255,92,92);TX=(241,244,248);SE=(154,167,184);YL=(255,200,87)
COL={'white':TX,'gray':SE,'green':GR,'red':RD,'yellow':YL}
spec=json.load(open(sys.argv[1]));OUT=sys.argv[2]
STILL=len(sys.argv)>3 and sys.argv[3]=='still'
DURS={'hook':3.0,'chart':5.6,'stamp':3.8,'steps':4.2}
scenes=[];t0=0.0
for s in spec['scenes']:
    d=DURS[s['type']];scenes.append(dict(s,t0=t0,t1=t0+d));t0+=d
scenes.append({'type':'end','t0':t0,'t1':t0+3.2});T_END=t0+3.2
def ease(x): x=max(0,min(1,x)); return 1-(1-x)**3
def back(x,s=1.9): x=max(0,min(1,x)); return 1+(s+1)*(x-1)**3+s*(x-1)**2
def clamp(x,a=0,b=1): return max(a,min(b,x))
yy=np.linspace(0,1,H)[:,None,None]
base=(np.array(BG)*(1-yy)+np.array((16,28,43))*yy)*np.ones((1,W,3))
gx,gy=np.meshgrid(np.arange(W),np.arange(H))
vig=1-0.55*(((gx-W/2)/(W*0.75))**2+((gy-H/2)/(H*0.75))**2)
BGA=(base*np.clip(vig,0.25,1)[...,None])
BGI=Image.fromarray(BGA.astype('uint8'));d=ImageDraw.Draw(BGI)
for x in range(0,W,90): d.line([(x,0),(x,H)],fill=(24,36,52),width=1)
for y in range(0,H,90): d.line([(0,y),(W,y)],fill=(24,36,52),width=1)
BGA=np.asarray(BGI).astype(np.int16)
def glow_blob(cx,cy,r,col,a):
    s=Image.new('RGB',(W//4,H//4),(0,0,0));dd=ImageDraw.Draw(s)
    dd.ellipse([(cx-r)/4,(cy-r)/4,(cx+r)/4,(cy+r)/4],fill=tuple(int(c*a) for c in col))
    return np.asarray(s.filter(ImageFilter.GaussianBlur(40)).resize((W,H),Image.BILINEAR)).astype(np.int16)
_tc={}
def timg(txt,font,col,glow=0):
    k=(txt,font.path,font.size,col,glow)
    if k in _tc: return _tc[k]
    w=int(font.getlength(txt))+120;h=int(font.size*1.5)+120
    im=Image.new('RGBA',(w,h),(0,0,0,0));ImageDraw.Draw(im).text((60,60),txt,font=font,fill=col+(255,))
    g=None
    if glow:
        g=Image.new('RGBA',(w,h),(0,0,0,0));ImageDraw.Draw(g).text((60,60),txt,font=font,fill=col+(255,));g=g.filter(ImageFilter.GaussianBlur(glow))
    _tc[k]=(im,g);return _tc[k]
def put(im,layer,cx,cy,sc=1.0,al=1.0):
    if al<=0.01: return
    if abs(sc-1)>0.005: layer=layer.resize((max(1,int(layer.width*sc)),max(1,int(layer.height*sc))),Image.BILINEAR)
    if al<0.99:
        a=layer.getchannel('A').point(lambda v:int(v*al));layer=layer.copy();layer.putalpha(a)
    im.paste(layer,(int(cx-layer.width/2),int(cy-layer.height/2)),layer)
def fit(txt,wt,size,maxw=W-150):
    s=size
    while s>36 and f(wt,s).getlength(txt)>maxw: s-=4
    return f(wt,s)
def text(im,txt,wt,size,col,cx,cy,t,t0,glow=0,out=1.0):
    k=t-t0
    if k<0 or not txt: return
    font=fit(txt,wt,size);sc=0.6+0.4*back(k/0.28);al=ease(k/0.18)*out;off=(1-ease(k/0.28))*30
    L,G=timg(txt,font,col,glow)
    if G is not None: put(im,G,cx,cy+off,sc,al*0.9)
    put(im,L,cx,cy+off,sc,al)
def shake(t,t0,dur=0.45,amp=26):
    k=t-t0
    if k<0 or k>dur: return (0,0)
    a=amp*(1-k/dur)**2;return (int(a*math.sin(k*90)),int(a*math.cos(k*77)))
# ---------- chart data per chart scene ----------
CX0,CX1,CY0,CY1=70,1010,600,1400
def build_chart(sc,idx):
    rng=np.random.default_rng(spec.get('seed',7)+idx*13)
    drift={'up':0.55,'down':-0.55,'range':0.0}.get(sc.get('trend','up'),0.55)
    o=[];c=[];hi=[];lo=[];p=100.0
    for i in range(24):
        dd=drift+rng.normal(0,0.9 if drift else 1.1);op=p;cl=p+dd
        hi.append(max(op,cl)+abs(rng.normal(0,0.5)));lo.append(min(op,cl)-abs(rng.normal(0,0.5)));o.append(op);c.append(cl);p=cl
    for i in range(5):
        op=p;cl=p+rng.normal(0.05*np.sign(drift),0.5);o.append(op);c.append(cl);hi.append(max(op,cl)+0.3);lo.append(min(op,cl)-0.3);p=cl
    last=c[-1];lv=[]
    for l in sc.get('levels',[]): lv.append(dict(label=l['label'],color=COL[l.get('color','white')],y=last+l.get('delta',0)))
    ev=sc.get('event','none');ref=None
    if ev=='gap_down':
        ref=min(l['y'] for l in lv) if lv else last-1
        go=ref-2.3;gc=go-1.6;o.append(go);c.append(gc);hi.append(go+0.2);lo.append(gc-0.9)
    elif ev=='gap_up':
        ref=max(l['y'] for l in lv) if lv else last+1
        go=ref+2.3;gc=go+1.6;o.append(go);c.append(gc);hi.append(gc+0.9);lo.append(go-0.2)
    N=len(o);pmin=min(min(lo),*[l['y'] for l in lv] or [999])-1.2;pmax=max(max(hi),*[l['y'] for l in lv] or [-999])+1.0
    py=lambda v:CY1-(v-pmin)/(pmax-pmin)*(CY1-CY0)
    return dict(o=o,c=c,hi=hi,lo=lo,N=N,py=py,lv=lv,ev=ev,ref=ref,go=(o[-1] if ev!='none' else None))
for i,s in enumerate(scenes):
    if s['type']=='chart': s['ch']=build_chart(s,i)
def draw_chart(im,ch,t,tstart,per,alpha=1.0,ghost=False,gap_t=None):
    d=ImageDraw.Draw(im,'RGBA');A=lambda a:int(255*a*alpha);N=ch['N'];CW=(CX1-CX0)/N;py=ch['py']
    n=int(clamp((t-tstart)/per,0,N))
    for i in range(n):
        up=ch['c'][i]>=ch['o'][i];col=GR if up else RD;x=CX0+i*CW+CW/2;prog=1.0
        if i==n-1 and not ghost: prog=ease(((t-tstart)-i*per)/per*1.5)
        d.line([(x,py(ch['hi'][i])),(x,py(ch['lo'][i]))],fill=col+(A(0.9*prog),),width=3)
        y1=py(max(ch['o'][i],ch['c'][i]));y2=py(min(ch['o'][i],ch['c'][i]));ym=(y1+y2)/2;y1=ym+(y1-ym)*prog;y2=ym+(y2-ym)*prog
        d.rectangle([x-CW*0.32,y1,x+CW*0.32,max(y2,y1+3)],fill=col+(A(prog),))
def dashed(im,y,col,a,prog=1.0):
    d=ImageDraw.Draw(im,'RGBA');xe=CX0+(CX1-CX0)*prog;x=CX0
    while x<xe: d.line([(x,y),(min(x+22,xe),y)],fill=col+(int(255*a),),width=4);x+=36
fb=f('Bold',46)
def chrome(im):
    d=ImageDraw.Draw(im,'RGBA');d.text((100,110),'kais',font=fb,fill=TX+(255,));d.text((100+fb.getlength('kais'),110),'trade',font=fb,fill=GR+(255,))
    fd=f('Regular',30);s='Crypto is high risk. Not financial advice.';d.text(((W-fd.getlength(s))/2,1830),s,font=fd,fill=SE+(150,))
EVENTS=[]  # (time, kind, arg)
def gap_time(s): 
    ch=s['ch'];return 0.4+0.085*(ch['N']-1)
def frame(t):
    s=next(x for x in scenes if x['t0']<=t<x['t1'] or x is scenes[-1]);k=t-s['t0'];dur=s['t1']-s['t0'];out=ease((dur-k)/0.25)
    gxx=W/2+300*math.sin(t*0.5);gyy=H*0.45+200*math.cos(t*0.4)
    arr=np.clip(BGA+glow_blob(gxx,gyy,300,(20,90,60),0.55),0,255).astype('uint8');im=Image.fromarray(arr)
    chrome(im);sx=sy=0;ty=s['type']
    if ty=='hook':
        text(im,s.get('pre',''),'SemiBold',70,SE,W/2,760,k,0.25,out=out)
        text(im,s['big'],'Bold',150,TX,W/2,930,k,0.6,glow=22,out=out)
        pc=COL[s.get('post_color','red')];text(im,s.get('post',''),'Bold',92,pc,W/2,1120,k,1.35,glow=18,out=out)
        sx,sy=shake(k,1.35,0.3,14)
        if k>1.9:
            d=ImageDraw.Draw(im,'RGBA');w=ease((k-1.9)/0.4)*620;d.rounded_rectangle([W/2-310,1200,W/2-310+w,1210],radius=5,fill=pc+(int(255*out),))
    elif ty=='chart':
        ch=s['ch'];N=ch['N'];CW=(CX1-CX0)/N;py=ch['py']
        text(im,s.get('title1',''),'Bold',72,TX,W/2,360,k,0.15,out=out)
        text(im,s.get('title2',''),'Bold',72,YL,W/2,455,k,0.45,glow=14,out=out)
        draw_chart(im,ch,k,0.4,0.085,alpha=out)
        for j,l in enumerate(ch['lv']):
            ts=0.9+0.4*j
            if k>ts:
                dashed(im,py(l['y']),l['color'],0.85*out,prog=ease((k-ts)/0.5))
                L,_=timg(l['label'],f('Bold',34),l['color']);put(im,L,CX0+70,py(l['y'])+(-34 if l['y']>=ch['lv'][0]['y'] else 34),1,ease((k-ts-0.3)/0.3)*out)
        if ch['ev']!='none':
            gt=gap_time(s)
            if k>=gt:
                sx,sy=shake(k,gt,0.5,30);fl=1-ease((k-gt)/0.25)
                if fl>0:
                    col=(255,60,60) if ch['ev']=='gap_down' else (52,199,123);im=Image.blend(im,Image.new('RGB',(W,H),col),0.22*fl*out)
                kk=ease((k-gt-0.3)/0.4)
                if kk>0:
                    x=CX1-CW*1.6;d=ImageDraw.Draw(im,'RGBA');ys=py(ch['ref']);ye=py(ch['go']);yc=ys+(ye-ys)*kk
                    d.line([(x,ys),(x,yc)],fill=YL+(255,),width=5);d.line([(x-14,yc),(x+14,yc)],fill=YL+(255,),width=5)
                text(im,s.get('event_label','GAP'),'Bold',40,YL,CX1-CW*1.6-130,(py(ch['ref'])+py(ch['go']))/2,k,gt+0.5,glow=10,out=out)
                text(im,s.get('line1',''),'SemiBold',50,TX,W/2,1560,k,gt+1.1,out=out)
                text(im,s.get('line2',''),'Regular',44,SE,W/2,1640,k,gt+1.6,out=out)
        else:
            text(im,s.get('line1',''),'SemiBold',50,TX,W/2,1560,k,2.9,out=out);text(im,s.get('line2',''),'Regular',44,SE,W/2,1640,k,3.4,out=out)
    elif ty=='stamp':
        prev=next((x for x in reversed(scenes) if x['type']=='chart' and x['t1']<=s['t0']),None)
        if prev: draw_chart(im,prev['ch'],99,0,0.01,alpha=0.2,ghost=True)
        cc=COL[s.get('color','green')]
        if k<0.7:
            d=ImageDraw.Draw(im,'RGBA');r=80+ease(k/0.7)*520;d.ellipse([W/2-r,720-r,W/2+r,720+r],outline=cc+(int(200*(1-k/0.7)),),width=6)
        L,G=timg(s.get('stamp','FACT'),f('Bold',84),cc,26);sc=0.3+0.7*back(k/0.3,2.6)
        put(im,G,W/2,720,sc,0.9*out*ease(k/0.1));put(im,L,W/2,720,sc,out*ease(k/0.08));sx,sy=shake(k,0,0.35,18)
        text(im,s.get('line1',''),'Bold',74,TX,W/2,960,k,0.6,out=out)
        text(im,s.get('line2',''),'Bold',74,COL[s.get('line2_color','green')],W/2,1070,k,1.3,glow=14,out=out)
        text(im,s.get('foot',''),'Regular',46,SE,W/2,1250,k,2.3,out=out)
    elif ty=='steps':
        text(im,s.get('title','Do this instead'),'Bold',84,TX,W/2,520,k,0.1,glow=12,out=out)
        items=s['items'][:3]
        for i,sn in enumerate(items):
            kk=k-(0.7+i*0.85)
            if kk<0: continue
            y=800+i*230;a=ease(kk/0.2)*out;scl=0.6+0.4*back(kk/0.3);d=ImageDraw.Draw(im,'RGBA')
            d.rounded_rectangle([90,y-85,W-90,y+85],radius=34,fill=(255,255,255,int(14*a)),outline=GR+(int(90*a),),width=2)
            cx=170;r=int(52*scl);d.ellipse([cx-r,y-r,cx+r,y+r],fill=GR+(int(255*a),))
            L,_=timg(str(i+1),f('Bold',58),(10,25,18));put(im,L,cx,y,scl,a)
            fnt=fit(sn,'SemiBold',48,W-90-cx-70-60);L,_=timg(sn,fnt,TX);put(im,L,cx+75+(L.width-120)/2+60,y,1,a)
        text(im,s.get('foot',''),'SemiBold',50,GR,W/2,1560,k,3.2,glow=10,out=out)
    else:
        a=ease(k/0.5);fw=f('Bold',160);w1=fw.getlength('kais');w2=fw.getlength('trade');x=(W-w1-w2)/2;pulse=0.5+0.5*math.sin(k*3)
        L1,G1=timg('kais',fw,TX,24);L2,G2=timg('trade',fw,GR,24);scl=0.85+0.15*back(k/0.5)
        put(im,G2,x+w1+w2/2,760,scl,(0.5+0.4*pulse)*a);put(im,L1,x+w1/2,760,scl,a);put(im,L2,x+w1+w2/2,760,scl,a)
        d=ImageDraw.Draw(im,'RGBA');fs=f('Medium',36);s_='CRYPTO TRADING INTELLIGENCE';d.text(((W-fs.getlength(s_))/2,930),s_,font=fs,fill=SE+(int(255*ease((k-0.3)/0.4)),))
        b=ease((k-0.7)/0.4);fl=f('SemiBold',56);s_='Link in bio';bw=fl.getlength(s_)+130;by=1100-int(8*pulse)
        d.rounded_rectangle([(W-bw)/2,by,(W+bw)/2,by+104],radius=52,fill=GR+(int(255*b*0.15),),outline=GR+(int(255*b),),width=4)
        d.text(((W-fl.getlength(s_))/2,by+17),s_,font=fl,fill=GR+(int(255*b),))
    if sx or sy: im=im.transform(im.size,Image.AFFINE,(1,0,-sx,0,1,-sy),resample=Image.BILINEAR)
    return im
# ---------- sound events from scene layout ----------
for s in scenes:
    t0_=s['t0']
    if s['type']!='end' and s is not scenes[0]: EVENTS.append((t0_-0.15,'riser',0))
    if s['type']=='end': EVENTS.append((t0_-0.15,'riser',0));EVENTS+=[(t0_+0.3,'bell',1319),(t0_+0.4,'bell',1760)]
    if s['type']=='hook': EVENTS+=[(t0_+0.25,'boom',0.55),(t0_+0.6,'boom',0.55),(t0_+1.35,'boom',0.55)]
    if s['type']=='chart' and s['ch']['ev']!='none': EVENTS.append((t0_+gap_time(s),'bigboom',1.0))
    if s['type']=='stamp': EVENTS.append((t0_,'bigboom',0.9))
    if s['type']=='steps':
        for i,fq in enumerate((784,988,1175)[:len(s['items'][:3])]): EVENTS.append((t0_+0.7+i*0.85,'bell',fq))
# ---------- audio ----------
sr=44100;dur=T_END+0.3;n_=int(sr*dur);BPM=spec.get('bpm',100);beat=60/BPM;bar=4*beat;R=np.random.default_rng(5)
def note(m): return 440*2**((m-69)/12)
def lp(x,fc,order=2): return signal.sosfilt(signal.butter(order,fc/(sr/2),'low',output='sos'),x)
def hp(x,fc,order=2): return signal.sosfilt(signal.butter(order,fc/(sr/2),'high',output='sos'),x)
L=np.zeros(n_);Rr=np.zeros(n_);FX=np.zeros(n_);bass=np.zeros(n_);kick_env=np.ones(n_)
def addto(buf,at,sig,g=1.0):
    s=int(at*sr)
    if s>=n_ or s<0: return
    seg=buf[s:s+len(sig)];seg+=sig[:len(seg)]*g
prog=[[[57,60,64,67],[53,57,60,64],[48,52,55,59],[55,59,62,66]],[[50,53,57,60],[58,62,65,69],[53,57,60,64],[48,52,55,59]]][spec.get('prog',0)]
for i in range(int(dur/bar)+1):
    ch=prog[i%4];at=i*bar;ln=int(bar*sr*1.05);tt=np.arange(ln)/sr;env=np.minimum(1,tt/0.35)*np.minimum(1,(bar*1.05-tt)/0.25);pl=np.zeros(ln);pr=np.zeros(ln)
    for m in ch[:3]:
        for d_,side in ((-0.12,0),(0.0,2),(0.12,1)):
            w=signal.sawtooth(2*np.pi*note(m)*2**(d_/12)*tt)
            if side in (0,2): pl+=w
            if side in (1,2): pr+=w
    addto(L,at,lp(pl,1400)*env*0.035);addto(Rr,at,lp(pr,1400)*env*0.035)
    fr=note(ch[0]-24)
    for hb in (0,2):
        a2=i*bar+hb*beat;l2=int(2*beat*sr);t2=np.arange(l2)/sr;s_=np.sin(2*np.pi*fr*t2)+0.25*np.sin(2*np.pi*2*fr*t2)
        addto(bass,a2,np.tanh(s_*1.6)*0.28*np.minimum(1,t2/0.01)*np.exp(-t2*1.6))
    if i>=1:
        for kx,oi in enumerate([0,1,2,3,2,1,2,3]):
            a2=i*bar+kx*beat/2;fq=note(ch[oi]+12);l2=int(0.45*sr);t2=np.arange(l2)/sr
            pk=(np.sin(2*np.pi*fq*t2)+0.5*np.sin(2*np.pi*2*fq*t2)+0.2*np.sin(2*np.pi*3*fq*t2))*np.exp(-t2*9)*np.minimum(1,t2/0.004)
            if kx%2==0: addto(L,a2,pk,0.07);addto(Rr,a2,pk,0.028)
            else: addto(Rr,a2,pk,0.07);addto(L,a2,pk,0.028)
            addto(FX,a2,pk,0.05)
def kick(at,g=0.55):
    ln=int(0.3*sr);tt=np.arange(ln)/sr;k_=np.sin(2*np.pi*(45+110*np.exp(-tt*35))*tt)*np.exp(-tt*10)*g+R.standard_normal(ln)*np.exp(-tt*120)*0.05
    addto(L,at,k_);addto(Rr,at,k_);s=int(at*sr);e=min(n_,s+int(0.22*sr))
    if s<n_: kick_env[s:e]*=np.minimum(1,0.35+np.linspace(0,0.65,e-s)**0.7)
def clap(at,g=0.22):
    ln=int(0.22*sr);tt=np.arange(ln)/sr;x=hp(lp(R.standard_normal(ln),6500),900)*(np.exp(-tt*28)+0.6*np.exp(-np.clip(tt-0.012,0,None)*40)*(tt>0.012))
    addto(L,at,x,g);addto(Rr,at,x,g);addto(FX,at,x,g*0.5)
def hat(at,g=0.05,op=False):
    ln=int((0.16 if op else 0.05)*sr);tt=np.arange(ln)/sr;x=hp(R.standard_normal(ln),7000)*np.exp(-tt*(25 if op else 110));addto(L,at,x,g);addto(Rr,at,x,g)
for b in range(int(dur/beat)):
    at=b*beat
    if b>=4:
        if b%4 in (0,2): kick(at)
        if b%4 in (1,3): clap(at)
        hat(at+beat/2,0.05,op=(b%4==3));hat(at,0.025)
    elif b>=2 and b%2==0: kick(at,0.4)
L=L*kick_env;Rr=Rr*kick_env;bass=bass*kick_env;L+=bass;Rr+=bass
def boom(at,g=1.0,ln=1.1):
    n=int(ln*sr);tt=np.arange(n)/sr
    sig=np.tanh(np.sin(2*np.pi*(34+130*np.exp(-tt*14))*tt)*np.exp(-tt*3.2)*1.3+hp(R.standard_normal(n),1500)*np.exp(-tt*40)*0.35+lp(R.standard_normal(n),500)*np.exp(-tt*9)*0.5)*g*0.8
    addto(L,at,sig);addto(Rr,at,sig);addto(FX,at,sig,0.5)
def riser(at,ln=0.5,g=0.5):
    n=int(ln*sr);tt=np.arange(n)/sr;x=R.standard_normal(n);o_=np.zeros(n)
    for i in range(0,n,2048):
        fc=300+6000*((i/n)**2);hi_=min(fc*1.6,sr/2-100);o_[i:i+2048]=signal.sosfilt(signal.butter(2,[fc/(sr/2),hi_/(sr/2)],'band',output='sos'),x[i:i+2048])
    o_*=(tt/ln)**1.5*np.minimum(1,(ln-tt)/0.03);addto(L,at,o_,g);addto(Rr,at,o_,g);addto(FX,at,o_,g*0.4)
def bell(at,fq,g=0.18):
    n=int(0.9*sr);tt=np.arange(n)/sr;sig=(np.sin(2*np.pi*fq*tt)+0.5*np.sin(2*np.pi*fq*2.76*tt)+0.3*np.sin(2*np.pi*fq*5.4*tt))*np.exp(-tt*5)*np.minimum(1,tt/0.003)
    addto(L,at,sig,g);addto(Rr,at,sig,g);addto(FX,at,sig,g*0.7)
    n2=int(0.12*sr);t2=np.arange(n2)/sr;pp=np.sin(2*np.pi*(220+900*np.exp(-t2*50))*t2)*np.exp(-t2*35);addto(L,at-0.02,pp,0.25);addto(Rr,at-0.02,pp,0.25)
for at,kind,arg in EVENTS:
    if kind=='boom': boom(max(at,0),arg,0.6)
    elif kind=='bigboom': boom(max(at,0),arg,1.3)
    elif kind=='riser': riser(max(at,0),0.5,0.5)
    elif kind=='bell': bell(at,arg,0.2 if arg>1200 else 0.18)
irl=int(1.4*sr);tt=np.arange(irl)/sr;ir=lp(R.standard_normal(irl)*np.exp(-tt*3.2),5000);ir/=np.sqrt((ir**2).sum())
rev=signal.fftconvolve(FX,ir)[:n_];L+=rev*0.55;Rr+=np.roll(rev,int(0.013*sr))*0.55
fi=int(0.5*sr);fo=int(1.6*sr)
for c_ in (L,Rr): c_[:fi]*=np.linspace(0,1,fi);c_[-fo:]*=np.linspace(1,0,fo)
st=np.tanh(np.stack([L,Rr],1)*1.25);st=st/np.max(np.abs(st))*0.9
tmp=tempfile.mkdtemp();wav=os.path.join(tmp,'m.wav')
with wave.open(wav,'wb') as w:
    w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr);w.writeframes((st*32767).astype('<i2').tobytes())
if STILL:
    for tt_ in sys.argv[4:]: frame(float(tt_)).save(f'still_{tt_}.png')
    sys.exit()
p=subprocess.Popen(['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-i',wav,'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-t',str(T_END),'-movflags','+faststart',OUT],stdin=subprocess.PIPE)
for i in range(int(T_END*FPS)): p.stdin.write(frame(i/FPS).convert('RGB').tobytes())
p.stdin.close();p.wait();print('done',round(T_END,1),'s',os.path.getsize(OUT)//1024,'KB')
