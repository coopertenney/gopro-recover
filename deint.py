"""De-interleave a GoPro MP4 whose clusters are mixed with its LRV sidecar on an exFAT card.
build_map(dev, start, mp4_moov, lrv_moov) -> list disk_cluster_index per file cluster (relative to start), stats."""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp4lib
CL=int(os.environ.get("CLUSTER","131072")); SEC=512
class Dev:
    def __init__(s,path): s.fh=open(path,'rb',buffering=0)
    def rd(s,off,n):
        a=off-(off%SEC); n2=((off+n-a)+SEC-1)//SEC*SEC
        s.fh.seek(a); b=s.fh.read(n2); return b[off-a:off-a+n]
def samples_of(moov):
    """-> list of (file_off, size, kind) sorted; kind in hevc/avc/devc/other"""
    tracks=mp4lib.parse_tracks(moov); mm=moov[8:] if moov[4:8]==b'moov' else moov
    out=[]
    for ts,te in mp4lib.find(mm,[b'trak']):
        sd=mp4lib.find(mm,[b'mdia',b'minf',b'stbl',b'stsd'],ts,te); codec=mm[sd[0][0]+12:sd[0][0]+16] if sd else b''
        hd=mp4lib.find(mm,[b'mdia',b'hdlr'],ts,te); handler=mm[hd[0][0]+8:hd[0][0]+12] if hd else b''
        kind={b'hvc1':'hevc',b'hev1':'hevc',b'avc1':'avc',b'gpmd':'devc'}.get(codec,'other')
        tr=[t for t in tracks if t.get('handler')==handler and t.get('chunks')]
        # match track by stbl position: rebuild from this trak directly
        t={}
        stbl=mp4lib.find(mm,[b'mdia',b'minf',b'stbl'],ts,te)[0]
        for tag in (b'stco',b'co64'):
            c=mp4lib.find(mm,[tag],*stbl)
            if c:
                s=c[0][0]; n=struct.unpack('>I',mm[s+4:s+8])[0]; w=4 if tag==b'stco' else 8
                t['chunks']=list(struct.unpack(('>%dI' if w==4 else '>%dQ')%n,mm[s+8:s+8+n*w]))
        z=mp4lib.find(mm,[b'stsz'],*stbl); s=z[0][0]; const,n=struct.unpack('>II',mm[s+4:s+12])
        t['sizes']=[const]*n if const else list(struct.unpack('>%dI'%n,mm[s+12:s+12+4*n]))
        sc=mp4lib.find(mm,[b'stsc'],*stbl); s=sc[0][0]; n=struct.unpack('>I',mm[s+4:s+8])[0]
        t['stsc']=[struct.unpack('>III',mm[s+8+12*i:s+20+12*i]) for i in range(n)]
        si=0; sizes=t['sizes']
        for ci,off,csize,_ in mp4lib.chunk_table(t):
            o=off
            # samples in this chunk
            spc=1
            for first,cnt,_ in t['stsc']:
                if ci+1>=first: spc=cnt
            for _ in range(spc):
                if si>=len(sizes): break
                out.append((o,sizes[si],kind)); o+=sizes[si]; si+=1
    out.sort(); return out
def hdr_ok(kind,b,remaining):
    """b = 5 bytes at NAL boundary (4 len + header). remaining = bytes left in sample incl these 4."""
    if kind=='devc': return b[:4]==b'DEVC'
    if len(b)<6: return None
    L=struct.unpack('>I',b[:4])[0]
    if L<=0 or L+4>remaining: return False
    b0,b1=b[4],b[5]
    if b0&0x80: return False
    if kind=='hevc':
        return ((b0>>1)&0x3f)<=40 and (b1&0xf8)==0 and (b1&7)>=1
    if kind=='avc':
        return 1<=(b0&0x1f)<=23
    return None
class Walker:
    """Tracks one file's expected content; validates candidate clusters for its next file cluster."""
    def __init__(s,samples,nclusters):
        s.samples=samples; s.n=nclusters
        s.by_cluster=[[] for _ in range(nclusters+1)]
        for off,size,kind in samples:
            if kind in('hevc','avc','devc') and off//CL<=nclusters: s.by_cluster[off//CL].append((off,size,kind))
        s.k=0          # next file cluster to place
        s.open=[]      # pending NAL boundaries inside open samples: (file_off, remaining, kind)
    def check(s,k,data,getbytes):
        """Validate cluster k content = data. getbytes(file_off,n) reads already-mapped bytes (for boundaries spanning). Returns 'ok','bad','unknown' and new pending list."""
        anchors=[]
        base=k*CL
        for off,size,kind in s.by_cluster[k]:
            anchors.append((off,size,kind))
        pend=[(o,r,kd) for (o,r,kd) in s.open if o//CL==k]
        anchors+=pend
        if not anchors: return 'unknown',None
        newopen=[(o,r,kd) for (o,r,kd) in s.open if o//CL>k]
        for off,rem,kind in anchors:
            i=off-base
            b=data[i:i+6]
            if len(b)<6:  # boundary straddles cluster end; need next cluster -> defer: treat as unknown-ish
                newopen.append((off,rem,kind)); continue
            r=hdr_ok(kind,b,rem)
            if r is False: return 'bad',None
            if r and kind!='devc':
                L=struct.unpack('>I',b[:4])[0]; nxt=off+4+L; nrem=rem-4-L
                if nrem>0: newopen.append((nxt,nrem,kind))
        return 'ok',newopen
def build_map(dev,start,mp4_moov,lrv_moov,log=print,max_extra=None):
    ms=samples_of(mp4_moov); mend=max(o+z for o,z,_ in ms); N=(mend+CL-1)//CL
    mw=Walker(ms,N)
    lw=None
    if lrv_moov is not None:
        ls=samples_of(lrv_moov); lend=max(o+z for o,z,_ in ls); lw=Walker(ls,(lend+CL-1)//CL)
    mapping=[]; d=0; stats={'lrv':0,'garbage':0,'unknown':0,'ok':0}
    limit=N+ (max_extra if max_extra else N//4+2000)
    lrv_hint=0
    while mw.k<N and d<limit:
        data=dev.rd(start+d*CL,CL)
        if len(data)<CL: log(f"EOF at disk cluster {d}"); break
        r,newopen=mw.check(mw.k,data,None)
        if r=='ok':
            mapping.append(d); mw.open=newopen; mw.k+=1; d+=1; stats['ok']+=1; continue
        # not ok: is it an LRV cluster?
        is_lrv=False
        if lw is not None:
            order=[lrv_hint]+[j for j in range(lw.n) if j!=lrv_hint]
            for j in order[:400]:
                rr,no=lw.check(j,data,None)
                if rr=='ok' and (len(lw.by_cluster[j])>=2 or j==lrv_hint):
                    is_lrv=True; lrv_hint=j+1; break
        if is_lrv:
            stats['lrv']+=1; d+=1; continue
        if r=='unknown':
            mapping.append(d); mw.k+=1; d+=1; stats['unknown']+=1; continue
        # bad and not LRV
        stats['garbage']+=1; d+=1
    stats['placed']=mw.k; stats['needed']=N; stats['disk_clusters']=d
    return mapping,stats
if __name__=='__main__':
    # self-test on a PhotoRec-carved contiguous head of a clip
    import re
    dev=Dev(sys.argv[1]); D=os.path.expanduser('~/Desktop/GoPro_moov_card1')
    mp4=open(f"{D}/moov_{sys.argv[2]}.bin",'rb').read(); lrv=open(f"{D}/moov_{sys.argv[3]}.bin",'rb').read() if len(sys.argv)>3 else None
    start=int(sys.argv[4]) if len(sys.argv)>4 else 0
    m,st=build_map(dev,start,mp4,lrv)
    print(st); print("first 60 map:",m[:60])
    skips=[(i,m[i]-m[i-1]-1) for i in range(1,len(m)) if m[i]-m[i-1]>1]
    print("foreign runs (file_cluster, run_len):",skips[:30], "total runs",len(skips))

def find_head(dev,ftyps,mw,min_run=5,max_k=12):
    """Return disk offset of the cluster holding file cluster 0, verified by a chain of following clusters."""
    best=None
    for p in ftyps:
        if mw.check(0,dev.rd(p,CL),None)[0]!='ok': continue
        run=0
        for k in range(1,max_k):
            r=mw.check(k,dev.rd(p+k*CL,CL),None)[0]
            if r=='bad': break
            run+=1
        if run>=min_run and (best is None or run>best[1]): best=(p,run)
    return best[0] if best else None
def build_map2(dev,start,mp4_moov,lrv_moov,log=print):
    ms=samples_of(mp4_moov); mend=max(o+z for o,z,_ in ms); N=(mend+CL-1)//CL
    mw=Walker(ms,N); lw=None; hint=0
    if lrv_moov is not None:
        ls=samples_of(lrv_moov); lw=Walker(ls,(max(o+z for o,z,_ in ls)+CL-1)//CL)
    def lrv_ok(j,data):
        lw.open=[]; return lw.check(j,data,None)[0]=='ok' and len(lw.by_cluster[j])>=2
    mapping=[]; d=0; st={'ok':0,'unknown':0,'lrv':0,'garbage':0}; limit=N+N//3+5000
    while mw.k<N and d<limit:
        data=dev.rd(start+d*CL,CL)
        if len(data)<CL: log("EOF"); break
        if lw is not None and hint<lw.n and lrv_ok(hint,data):
            st['lrv']+=1; hint+=1; d+=1; continue
        r,newopen=mw.check(mw.k,data,None)
        if r=='ok':
            mapping.append(d); mw.open=newopen; mw.k+=1; d+=1; st['ok']+=1; continue
        if lw is not None:
            found=None
            for j in range(lw.n):
                if lrv_ok(j,data): found=j; break
            if found is not None:
                st['lrv']+=1; hint=found+1; d+=1; continue
        if r=='unknown':
            mapping.append(d); mw.open=[o for o in mw.open if o[0]//CL>mw.k]; mw.k+=1; d+=1; st['unknown']+=1; continue
        st['garbage']+=1; d+=1
    st.update(placed=mw.k,needed=N,disk_clusters=d)
    return mapping,st
def file_reader(dev,start,mapping,mend):
    """yield file bytes [36, mend) in order using mapping."""
    def rd(f,n):
        out=bytearray()
        while n>0:
            k=f//CL; i=f%CL; take=min(n,CL-i)
            out+=dev.rd(start+mapping[k]*CL+i,take); f+=take; n-=take
        return bytes(out)
    return rd

AUD=b'\x00\x00\x00\x02\x09'
def foreign_sig(data,mw,k):
    """True if cluster contains an H.264 access-unit-delimiter at a position where file cluster k has no sample start."""
    expected={o%CL for o,z,kd in (mw.by_cluster[k] if k<len(mw.by_cluster) else [])}
    p=0
    while True:
        j=data.find(AUD,p)
        if j<0: return False
        if j not in expected and j+9<len(data):
            L=struct.unpack('>I',data[j+6:j+10])[0]
            if 0<L<2_000_000 and (data[j+10]&0x1f) in (1,5,6,7,8) if j+10<len(data) else True: return True
        p=j+1
def _walk(dev,start,mw,lw,limit,log=None,resync=True):
    """Core walker. Returns mapping (list, None = missing), stats."""
    mapping=[]; d=0; hint=0; st={'ok':0,'unknown':0,'lrv':0,'garbage':0,'jpeg':0,'resync':0,'evict':0}
    def lrv_ok(j,data):
        lw.open=[]; return lw.check(j,data,None)[0]=='ok' and len(lw.by_cluster[j])>=2
    unknown_run=0; bad_run=0
    while mw.k<mw.n and d<limit:
        data=dev.rd(start+d*CL,CL)
        if len(data)<CL: break
        if data[:3]==b'\xff\xd8\xff': st['jpeg']+=1; d+=1; continue
        if foreign_sig(data, mw, mw.k): st['foreign']=st.get('foreign',0)+1; d+=1; continue
        if lw is not None and hint<lw.n and lrv_ok(hint,data):
            st['lrv']+=1; hint+=1; d+=1; bad_run=0; continue
        r,newopen=mw.check(mw.k,data,None)
        if r=='ok':
            mapping.append(d); mw.open=newopen; mw.k+=1; d+=1; st['ok']+=1; unknown_run=0; bad_run=0; continue
        if lw is not None:
            found=None
            for j in range(lw.n):
                if lrv_ok(j,data): found=j; break
            if found is not None:
                st['lrv']+=1; hint=found+1; d+=1; bad_run=0; continue
        if r=='unknown':
            mapping.append(d); mw.open=[o for o in mw.open if o[0]//CL>mw.k]; mw.k+=1; d+=1; st['unknown']+=1; unknown_run+=1; continue
        # bad: if previous clusters were tentative, maybe a foreign cluster hid among them: evict last tentative and retry k-1 here
        if unknown_run>0 and bad_run==0:
            # try: does cluster d validate as k-1 ... k-unknown_run ? pick the largest k' that validates, evict accordingly
            for back in range(1,unknown_run+1):
                kk=mw.k-back
                rr,no=mw.check(kk,data,None)
                if rr=='ok':
                    # shift mapping entries for kk..k-1 by one: evict mapping[kk]
                    del mapping[kk]; mapping.append(d)
                    mw.k=kk+1; mw.open=no; d+=1; st['evict']+=1; unknown_run=0; bad_run=0
                    break
            else:
                st['garbage']+=1; d+=1; bad_run+=1
            continue
        st['garbage']+=1; d+=1; bad_run+=1
        if resync and bad_run>=48:
            # lost: look ahead for which file cluster this disk cluster could be
            cand=[k for k in range(mw.k,min(mw.n,mw.k+60000)) if mw.by_cluster[k]]
            hit=None
            for k in cand:
                mw.open=[]
                if mw.check(k,data,None)[0]=='ok':
                    # confirm with the next anchored cluster
                    nks=[x for x in range(k+1,min(mw.n,k+400)) if mw.by_cluster[x]][:2]
                    if len(nks)<2: continue
                    conf=0
                    for nk in nks:
                        mw.open=[]
                        if any(mw.check(nk,dev.rd(start+(d-1+(nk-k)+s)*CL,CL),None)[0]=='ok' for s in range(0,3)): conf+=1
                    if conf<2: continue
                    hit=k; break
            if hit is not None:
                while len(mapping)<hit: mapping.append(None)
                mapping.append(d-1); mw.k=hit+1; mw.open=[]; st['resync']+=1; bad_run=0
                if log: log(f"resync at disk {d-1} -> file cluster {hit}")
    st.update(placed=sum(1 for x in mapping if x is not None),needed=mw.n,disk_clusters=d)
    return mapping,st
def find_head2(dev,ftyps,ms,N,lrv_moov=None):
    """Score each ftyp candidate by a short walk; return (start, ok_count)."""
    best=None
    for p in ftyps:
        mw=Walker(ms,N)
        if mw.check(0,dev.rd(p,CL),None)[0]!='ok': continue
        lw=None
        if lrv_moov is not None:
            ls=samples_of(lrv_moov); lw=Walker(ls,(max(o+z for o,z,_ in ls)+CL-1)//CL)
        m,st=_walk(dev,p,mw,lw,limit=90,resync=False)
        score=st['ok']
        if best is None or score>best[1]: best=(p,score)
    return best if best and best[1]>=6 else None
def build_map3(dev,start,mp4_moov,lrv_moov,log=None):
    ms=samples_of(mp4_moov); N=(max(o+z for o,z,_ in ms)+CL-1)//CL; mw=Walker(ms,N)
    lw=None
    if lrv_moov is not None:
        ls=samples_of(lrv_moov); lw=Walker(ls,(max(o+z for o,z,_ in ls)+CL-1)//CL)
    return _walk(dev,start,mw,lw,limit=N+N//3+8000,log=log)
def file_reader2(dev,start,mapping,mend):
    def rd(f,n):
        out=bytearray()
        while n>0:
            k=f//CL; i=f%CL; take=min(n,CL-i)
            dc=mapping[k] if k<len(mapping) else None
            out+= dev.rd(start+dc*CL+i,take) if dc is not None else bytes(take)
            f+=take; n-=take
        return bytes(out)
    return rd
