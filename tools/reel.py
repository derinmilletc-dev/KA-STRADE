#!/usr/bin/env python3
"""KaisTrade Reel maker. Usage: python3 reel.py spec.json out.mp4
spec: {"bpm":96,"seed":1,"scenes":[{"dur":3.2,"els":[["pill","MYTH","red",0.0],["h","text","white|red|green",0.25,96],["s","sub",...],["t","title"...],["b","bullet"...]]}, ...]}
Element = [kind, text, color(white|gray|green|red), appear_time, optional font size]. Last scene is auto end card (kaistrade + Link in bio + disclaimer)."""
import sys, json, os, math, wave, subprocess, shutil, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont
F='/usr/share/fonts/opentype/inter/Inter-'
def f(w,s): return ImageFont.truetype(F+w+'.otf',s)
W,H,M,FPS=1080,1920,100,30
BG=(15,23,34);BG2=(16,28,43)
COL={'white':(241,244,248),'gray':(154,167,184),'green':(52,199,123),'red':(255,107,107)}
TX,SE,GR=COL['white'],COL['gray'],COL['green']
STYLE={'h':('Bold',96),'s':('Regular',48),'t':('Bold',72),'b':('SemiBold',56)}
spec=json.load(open(sys.argv[1])); out=sys.argv[2]
scenes=[(s['dur'],s['els']) for s in spec['scenes']]+[(3.0,[['end']])]
total=sum(d for d,_ in scenes)
# ---- music (original, royalty-free synthesis) ----
sr=44100; bpm=spec.get('bpm',96); beat=60/bpm; dur=total
t=np.arange(int(sr*dur))/sr; mus=np.zeros_like(t)
def note(m): return 440*2**((m-69)/12)
prog=[[[57,60,64],[53,57,60],[48,52,55],[55,59,62]],[[50,53,57],[58,62,65],[53,57,60],[48,52,55]]][spec.get('prog',0)]
bar=4*beat
for i in range(int(dur/bar)+1):
    ch=prog[i%4]; s=int(i*bar*sr); e=min(int((i+1)*bar*sr),len(t))
    if s>=len(t): break
    tt=t[s:e]-t[s]; env=np.minimum(1,tt/0.6)*np.exp(-tt*0.25)
    for m in ch:
        fr=note(m); mus[s:e]+=0.10*env*(np.sin(2*np.pi*fr*tt)+0.3*np.sin(2*np.pi*2*fr*tt)+0.15*np.sin(2*np.pi*fr*1.003*tt))
    fr=note(ch[0]-12); mus[s:e]+=0.18*np.minimum(1,tt/0.05)*np.exp(-tt*0.6)*np.sin(2*np.pi*fr*tt)
rng=np.random.default_rng(spec.get('seed',1))
for b in range(int(dur/beat)):
    st=int(b*beat*sr)
    if b>=4 and b%2==0:
        n=int(0.25*sr); tt=np.arange(n)/sr; k=np.sin(2*np.pi*(50+90*np.exp(-tt*30))*tt)*np.exp(-tt*12)*0.45
        seg=mus[st:st+n]; seg+=k[:len(seg)]
    if b>=4:
        for h in (0,0.5):
            hs=st+int(h*beat*sr); n=int(0.05*sr); hh=rng.standard_normal(n)*np.exp(-np.arange(n)/sr*90)*0.04
            seg=mus[hs:hs+n]; seg+=hh[:len(seg)]
fi=int(sr); fo=int(1.5*sr); mus[:fi]*=np.linspace(0,1,fi); mus[-fo:]*=np.linspace(1,0,fo)
mus=mus/np.max(np.abs(mus))*0.8
tmp=tempfile.mkdtemp(); wav=os.path.join(tmp,'m.wav')
with wave.open(wav,'wb') as w:
    w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr);w.writeframes((np.stack([mus,mus],1)*32767).astype('<i2').tobytes())
# ---- frames ----
grad=Image.new('RGB',(W,H));gd=ImageDraw.Draw(grad)
for y in range(H):
    k=y/H;gd.line([(0,y),(W,y)],fill=tuple(int(BG[i]*(1-k)+BG2[i]*k) for i in range(3)))
def wrap(txt,font,maxw):
    o=[];line=''
    for w in txt.split(' '):
        tt=(line+' '+w).strip()
        if font.getlength(tt)<=maxw: line=tt
        else: o.append(line);line=w
    o.append(line);return o
def ease(x): x=max(0,min(1,x)); return 1-(1-x)**3
fr_dir=os.path.join(tmp,'fr');os.makedirs(fr_dir);n=0
for dur_s,els in scenes:
    for i in range(int(dur_s*FPS)):
        tt=i/FPS;im=grad.copy();d=ImageDraw.Draw(im,'RGBA')
        fb=f('Bold',46);d.text((M,120),'kais',font=fb,fill=TX);d.text((M+fb.getlength('kais'),120),'trade',font=fb,fill=GR)
        oa=ease((dur_s-tt)/0.25)
        if els[0][0]=='end':
            a=ease(tt/0.5)*oa;fw=f('Bold',150);w1=fw.getlength('kais');w2=fw.getlength('trade');x=(W-w1-w2)/2
            d.text((x,700),'kais',font=fw,fill=TX+(int(255*a),));d.text((x+w1,700),'trade',font=fw,fill=GR+(int(255*a),))
            fs=f('Medium',36);s='CRYPTO TRADING INTELLIGENCE';d.text(((W-fs.getlength(s))/2,900),s,font=fs,fill=SE+(int(255*a),))
            b=ease((tt-0.5)/0.5)*oa;fl=f('SemiBold',54);s='Link in bio';bw=fl.getlength(s)+120
            d.rounded_rectangle([(W-bw)/2,1060,(W+bw)/2,1160],radius=50,outline=GR+(int(255*b),),width=4)
            d.text(((W-fl.getlength(s))/2,1080),s,font=fl,fill=GR+(int(255*b),))
            fd=f('Regular',36);s='Crypto is high risk. Not financial advice.';d.text(((W-fd.getlength(s))/2,1640),s,font=fd,fill=SE+(int(255*a),))
        else:
            blocks=[]
            for e in els:
                k,txt,cn,ap=e[0],e[1],e[2],e[3];col=COL[cn];fw_,fs_=STYLE.get(k,('Bold',72));
                if len(e)>4: fs_=e[4]
                font=f(fw_,fs_)
                if k=='pill': blocks.append((k,txt,font,col,ap,[txt],90))
                elif k=='b': ls=wrap(txt,font,W-2*M-70);blocks.append((k,txt,font,col,ap,ls,len(ls)*int(fs_*1.25)+30))
                else: ls=wrap(txt,font,W-2*M);blocks.append((k,txt,font,col,ap,ls,len(ls)*int(fs_*1.2)+40))
            y=(H-sum(b[-1] for b in blocks))/2
            for k,txt,font,col,ap,ls,h in blocks:
                a=ease((tt-ap)/0.45)*oa;dy=40*(1-ease((tt-ap)/0.45));al=int(255*a)
                if k=='pill':
                    fp=f('Bold',44);pw=fp.getlength(txt)+70
                    d.rounded_rectangle([M,y+dy,M+pw,y+dy+72],radius=36,fill=col+(int(50*a),),outline=col+(al,),width=3)
                    d.text((M+35,y+dy+11),txt,font=fp,fill=col+(al,))
                else:
                    x=M
                    if k=='b': d.ellipse([M,y+dy+font.size*0.35,M+28,y+dy+font.size*0.35+28],fill=GR+(al,));x=M+70
                    yy=y+dy
                    for l in ls: d.text((x,yy),l,font=font,fill=col+(al,));yy+=int(font.size*(1.25 if k=='b' else 1.2))
                y+=h
        im.save(os.path.join(fr_dir,f'{n:05d}.png'));n+=1
subprocess.run(['ffmpeg','-y','-loglevel','error','-framerate',str(FPS),'-i',os.path.join(fr_dir,'%05d.png'),'-i',wav,'-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-shortest','-movflags','+faststart',out],check=True)
shutil.rmtree(tmp);print('ok',round(total,1),'s',os.path.getsize(out)//1024,'KB')
