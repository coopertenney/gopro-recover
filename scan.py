# Scan a raw card for MP4 'moov' indexes and 'ftyp' headers. Read-only.
import struct, os, sys, time, datetime
DEV=sys.argv[1] if len(sys.argv)>1 else "/dev/rdisk4"
TAG=sys.argv[2] if len(sys.argv)>2 else "card1"
OUT=os.path.expanduser(f"~/Desktop/GoPro_moov_{TAG}"); os.makedirs(OUT,exist_ok=True)
LOG=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),f"scan_{TAG}.log"),"w")
def log(*a):
    s=" ".join(str(x) for x in a); print(s,flush=True); LOG.write(s+"\n"); LOG.flush()
SEC=512; CH=32*1024*1024; OV=64
fh=open(DEV,'rb',buffering=0)
import subprocess,re
total=0
if DEV.startswith('/dev/'):
    o=subprocess.run(['diskutil','info',DEV.replace('/dev/r','/dev/')],capture_output=True,text=True).stdout
    m=re.search(r'Disk Size:.*?\((\d+) Bytes',o); total=int(m.group(1)) if m else 0
else:
    total=os.path.getsize(DEV)
if not total: total=1
log(f"device {DEV} size {total/1e9:.1f} GB")
def rd(off,n):
    keep=fh.tell()
    a=off-(off%SEC); n2=((off+n-a)+SEC-1)//SEC*SEC
    fh.seek(a); b=fh.read(n2); fh.seek(keep); return b[off-a:off-a+n]
EPOCH=datetime.datetime(1904,1,1,tzinfo=datetime.timezone.utc)
def parse_moov(m):
    info={}
    # mvhd
    i=m.find(b'mvhd')
    if i>0:
        v=m[i+4]
        if v==0: ct,mt,ts,du=struct.unpack('>IIII',m[i+8:i+24])
        else: ct,mt,ts,du=struct.unpack('>QQIQ',m[i+8:i+36])
        info['created']=(EPOCH+datetime.timedelta(seconds=ct)).strftime('%Y-%m-%d %H:%M:%S')
        info['duration_s']=round(du/ts,1) if ts else None
    # chunk offsets
    mx=0; mn=None; pos=0
    for tag in (b'stco',b'co64'):
        pos=0
        while True:
            j=m.find(tag,pos)
            if j<0: break
            n=struct.unpack('>I',m[j+8:j+12])[0]
            if n and n<10_000_000:
                fmt='>%dI'%n if tag==b'stco' else '>%dQ'%n
                w=4 if tag==b'stco' else 8
                try:
                    offs=struct.unpack(fmt,m[j+12:j+12+n*w])
                    mx=max(mx,max(offs)); mn=min(offs) if mn is None else min(mn,min(offs))
                except Exception: pass
            pos=j+4
    info['first_chunk']=mn; info['last_chunk']=mx
    k=m.find(b'FIRM')
    if k>0: info['firmware']=m[k+4:k+12].decode('latin1','replace').strip('\x00')
    return info
ftyps=[]; moovs=[]
t0=time.time(); off=0; prev=b''
while True:
    b=fh.read(CH)
    if not b: break
    buf=prev+b; base=off-len(prev)
    p=0
    while True:
        j=buf.find(b'ftyp',p)
        if j<0: break
        if (base+j-4)%SEC==0 and j>=4:
            ftyps.append(base+j-4)
        p=j+4
    p=0
    while True:
        j=buf.find(b'moov',p)
        if j<0: break
        if j>=4 and buf[j+8:j+12]==b'mvhd':
            size=struct.unpack('>I',buf[j-4:j])[0]
            if 100<size<300*1024*1024:
                moff=base+j-4
                m=rd(moff,size)
                info=parse_moov(m)
                open(f"{OUT}/moov_{moff}.bin","wb").write(m)
                moovs.append((moff,size,info))
                log(f"MOOV @ {moff} size={size} {info}")
        p=j+4
    prev=buf[-OV:]; off+=len(b)
    if (off//CH)%32==0:
        log(f"... {off/1e9:.1f}/{total/1e9:.1f} GB  {off/1e6/(time.time()-t0):.0f} MB/s  ftyp={len(ftyps)} moov={len(moovs)}")
log(f"DONE in {time.time()-t0:.0f}s: {len(ftyps)} ftyp headers, {len(moovs)} moov indexes")
log("FTYP offsets:", ftyps)
# match: for each moov, find ftyp p < moff with moff - p close to last_chunk
log("\nMATCHES (file_start, moov_off, est_len_GB, created, duration):")
for moff,size,info in moovs:
    lc=info.get('last_chunk') or 0
    cands=[p for p in ftyps if p<moff and 0 <= (moff-p)-lc < 64*1024*1024]
    log(f"  moov@{moff}: start={cands[-1] if cands else None} est_len={(moff-(cands[-1] if cands else moff))/1e9:.2f}GB {info.get('created')} {info.get('duration_s')}s")
