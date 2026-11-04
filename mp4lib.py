import struct
def atoms(b, start=0, end=None):
    end=len(b) if end is None else end; i=start
    while i+8<=end:
        n,t=struct.unpack('>I4s',b[i:i+8]); h=8
        if n==1: n=struct.unpack('>Q',b[i+8:i+16])[0]; h=16
        elif n==0: n=end-i
        if n<8: break
        yield t,i+h,i+n
        i+=n
def find(b,path,start=0,end=None):
    """path list of tags; returns list of (payload_start,payload_end)"""
    res=[(start,len(b) if end is None else end)]
    for tag in path:
        nxt=[]
        for s,e in res:
            for t,ps,pe in atoms(b,s,e):
                if t==tag: nxt.append((ps,pe))
        res=nxt
    return res
def parse_tracks(moov):
    """moov = bytes of moov payload (after 8-byte header) or full atom."""
    if moov[4:8]==b'moov': moov=moov[8:]
    tracks=[]
    for ts,te in find(moov,[b'trak']):
        tr={}
        h=find(moov,[b'mdia',b'hdlr'],ts,te)
        if h: s=h[0][0]; tr['handler']=moov[s+8:s+12]; tr['name']=moov[s+24:h[0][1]].rstrip(b'\x00').decode('latin1','replace')
        stbl=find(moov,[b'mdia',b'minf',b'stbl'],ts,te)
        if not stbl: continue
        ss,se=stbl[0]
        for tag in (b'stco',b'co64'):
            c=find(moov,[tag],ss,se)
            if c:
                s=c[0][0]; n=struct.unpack('>I',moov[s+4:s+8])[0]
                w=4 if tag==b'stco' else 8; fmt=('>%dI' if tag==b'stco' else '>%dQ')%n
                tr['chunks']=list(struct.unpack(fmt,moov[s+8:s+8+n*w]))
        z=find(moov,[b'stsz'],ss,se)
        if z:
            s=z[0][0]; const,n=struct.unpack('>II',moov[s+4:s+12])
            tr['sizes']=[const]*n if const else list(struct.unpack('>%dI'%n,moov[s+12:s+12+4*n]))
        sc=find(moov,[b'stsc'],ss,se)
        if sc:
            s=sc[0][0]; n=struct.unpack('>I',moov[s+4:s+8])[0]
            tr['stsc']=[struct.unpack('>III',moov[s+8+12*i:s+20+12*i]) for i in range(n)]
        tracks.append(tr)
    return tracks
def chunk_table(tr):
    """yield (chunk_index, file_offset, chunk_size, first_sample_size)"""
    chunks=tr.get('chunks',[]); sizes=tr.get('sizes',[]); stsc=tr.get('stsc',[])
    si=0; out=[]
    for ci in range(len(chunks)):
        spc=1
        for k,(first,cnt,_) in enumerate(stsc):
            if ci+1>=first: spc=cnt
        n=sizes[si:si+spc]; si+=spc
        out.append((ci,chunks[ci],sum(n),n[0] if n else 0))
    return out
def mdat_end(tracks):
    m=0
    for tr in tracks:
        ct=chunk_table(tr)
        if ct: m=max(m,max(o+s for _,o,s,_ in ct))
    return m
def video_dims(moov):
    if moov[4:8]==b'moov': moov=moov[8:]
    best=(0,0)
    for ts,te in find(moov,[b'trak']):
        h=find(moov,[b'mdia',b'hdlr'],ts,te)
        if not h or moov[h[0][0]+8:h[0][0]+12]!=b'vide': continue
        k=find(moov,[b'tkhd'],ts,te)
        if k: s,e=k[0]; w,hh=struct.unpack('>II',moov[e-8:e]); best=(w>>16,hh>>16)
    return best
def mvhd_info(moov):
    import datetime
    if moov[4:8]==b'moov': moov=moov[8:]
    k=find(moov,[b'mvhd'])
    if not k: return None,None
    s=k[0][0]; v=moov[s]
    if v==0: ct,mt,ts,du=struct.unpack('>IIII',moov[s+4:s+20])
    else: ct,mt,ts,du=struct.unpack('>QQIQ',moov[s+4:s+32])
    created=(datetime.datetime(1904,1,1)+datetime.timedelta(seconds=ct)).strftime('%Y-%m-%d_%H-%M-%S')
    return created, (du/ts if ts else 0)
