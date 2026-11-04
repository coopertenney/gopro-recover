# usage: python3 -I extract2.py /dev/rdisk4 card3 <clip#> <out.mp4>
import sys,os,re,json,struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import deint, mp4lib
DEV,TAG,N,OUT=sys.argv[1],sys.argv[2],int(sys.argv[3]),sys.argv[4]; CL=131072
HERE=os.path.dirname(os.path.abspath(__file__)); HOME=os.path.expanduser('~')
idx=open(f"{HOME}/Desktop/GoPro_clips_{TAG}/index.txt").read()
# clip number -> moov offset via maps order: rebuild same ordering as preview2
MOOVDIR=f"{HOME}/Desktop/GoPro_moov_{TAG}"; rows=[]
for fn in os.listdir(MOOVDIR):
    moff=int(re.findall(r'\d+',fn)[0]); m=open(os.path.join(MOOVDIR,fn),'rb').read()
    try: w,h=mp4lib.video_dims(m); c,d=mp4lib.mvhd_info(m); me=mp4lib.mdat_end(mp4lib.parse_tracks(m))
    except Exception: continue
    rows.append(dict(moff=moff,moov=m,w=w,h=h,created=c,dur=d or 0,mend=me))
rows.sort(key=lambda r:r['moff'])
clips=[r for r in rows if r['w']>=1900 and r['dur']>=45]
c=clips[N-1]; mp=json.load(open(f"{HERE}/maps/{TAG}_{c['moff']}.json"))
start,mapping=mp['start'],mp['map']
print(f"clip {N}: {c['created']} {c['dur']:.1f}s {c['mend']/1e9:.2f}GB placed={sum(1 for x in mapping if x is not None)}/{(c['mend']+CL-1)//CL}")
dev=deint.Dev(DEV); rd=deint.file_reader2(dev,start,mapping,c['mend'])
hdr=rd(0,28) if mapping and mapping[0] is not None else b'\x00\x00\x00\x14ftypmp41\x00\x00\x00\x00mp41\x00\x00\x00\x08free'
with open(OUT,'wb') as o:
    o.write(hdr); o.write(struct.pack('>I4s',c['mend']-28,b'mdat'))
    f=36
    while f<c['mend']:
        n=min(64*CL,c['mend']-f); o.write(rd(f,n)); f+=n
    o.write(c['moov'])
print("wrote",OUT,os.path.getsize(OUT)/1e9,"GB")
