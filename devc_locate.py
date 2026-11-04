import sys,os,re,json
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
        clips[moff]=dict(created=c,dur=d,table=table,hits=[])
    except Exception: pass
total=63596134400; CH=256*CL; off=0
while off<total:
    b=dev.rd(off,min(CH,total-off))
    if not b: break
    p=0
    while True:
        j=b.find(b'DEVC',p)
        if j<0: break
        dc=(off+j)//CL; ic=(off+j)%CL
        for moff,c in clips.items():
            if ic in c['table']:
                for fk in c['table'][ic]: c['hits'].append((dc,fk))
        p=j+4
    off+=len(b)
out={str(moff):dict(created=c['created'],dur=c['dur'],hits=c['hits']) for moff,c in clips.items()}
json.dump(out,open(f"{os.path.dirname(os.path.abspath(__file__))}/devc_hits_{TAG}.json",'w'))
for moff,c in sorted(clips.items()):
    hs=sorted(c['hits']); 
    # keep hits whose disk-file shift is shared by >=3 others (consistent runs)
    from collections import Counter
    sh=Counter(dc-fk for dc,fk in hs)
    good=[(dc,fk) for dc,fk in hs if sh[dc-fk]>=3]
    print(f"moov@{moff} {c['created']} {c['dur']:.0f}s hits={len(hs)} consistent={len(good)} disk_range={(min(d for d,_ in good),max(d for d,_ in good)) if good else None} moov_cluster={moff//CL} shifts={sh.most_common(4)}")
