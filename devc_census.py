import sys,os,re
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__))); import deint, mp4lib
DEV,TAG=sys.argv[1],sys.argv[2]; CL=131072
dev=deint.Dev(DEV); D=os.path.expanduser(f'~/Desktop/GoPro_moov_{TAG}')
clips={}
for fn in os.listdir(D):
    moff=int(re.findall(r'\d+',fn)[0]); m=open(f"{D}/{fn}",'rb').read()
    try:
        w,h=mp4lib.video_dims(m); c,d=mp4lib.mvhd_info(m)
        if w<1900 or (d or 0)<45: continue
        ss=deint.samples_of(m); table={}
        for o,z,kd in ss:
            if kd=='devc': table.setdefault(o%CL,[]).append(o//CL)
        clips[moff]=dict(created=c,dur=d,table=table,hits=0,expected=sum(len(v) for v in table.values()))
    except Exception: pass
import subprocess
total=63596134400; CH=256*CL; off=0
while off<total:
    b=dev.rd(off,min(CH,total-off))
    if not b: break
    p=0
    while True:
        j=b.find(b'DEVC',p)
        if j<0: break
        ic=(off+j)%CL
        for moff,c in clips.items():
            if ic in c['table']: c['hits']+=1
        p=j+4
    off+=len(b)
print(f"{'moov_off':>12} {'camera_clock':20} {'dur':>6} {'markers_found':>14} {'expected':>8}")
for moff,c in sorted(clips.items()):
    print(f"{moff:12} {c['created']:20} {c['dur']:6.0f} {c['hits']:14} {c['expected']:8}")
