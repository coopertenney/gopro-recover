# usage: python3 -I preview2.py /dev/rdisk4 card1 [min_seconds]
import sys,os,re,json,struct,subprocess,time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deint, mp4lib
DEV,TAG=sys.argv[1],sys.argv[2]; MINS=float(sys.argv[3]) if len(sys.argv)>3 else 45; START_N=int(sys.argv[4]) if len(sys.argv)>4 else 1; ONLY=set(int(x) for x in os.environ.get('ONLY','').split(',') if x)
HERE=os.path.dirname(os.path.abspath(__file__)); HOME=os.path.expanduser('~')
MOOVDIR=f"{HOME}/Desktop/GoPro_moov_{TAG}"; OUT=f"{HOME}/Desktop/GoPro_clips_{TAG}"; os.makedirs(OUT,exist_ok=True); os.makedirs(f"{HERE}/maps",exist_ok=True)
log=open(f"{HERE}/scan_{TAG}.log").read(); ftyps=sorted(eval(re.search(r"FTYP offsets: (\[.*?\])",log,re.S).group(1)))
dev=deint.Dev(DEV); CL=deint.CL
idx=open(f"{OUT}/index.txt","a" if START_N>1 else "w")
def line(s): print(s,flush=True); idx.write(s+"\n"); idx.flush()
rows=[]
for fn in os.listdir(MOOVDIR):
    moff=int(re.findall(r'\d+',fn)[0]); m=open(os.path.join(MOOVDIR,fn),'rb').read()
    try: w,h=mp4lib.video_dims(m); c,d=mp4lib.mvhd_info(m); me=mp4lib.mdat_end(mp4lib.parse_tracks(m))
    except Exception: continue
    rows.append(dict(moff=moff,moov=m,w=w,h=h,created=c,dur=d or 0,mend=me))
rows.sort(key=lambda r:r['moff'])
clips=[]
for i,r in enumerate(rows):
    if r['w']<1900 or r['dur']<MINS: continue
    lrv=[x for x in rows[i+1:i+3] if x['w']<1000 and abs(x['dur']-r['dur'])<1.5]
    r['lrv']=lrv[0]['moov'] if lrv else None; clips.append(r)
line(f"{len(clips)} clips >= {MINS}s on {TAG}")
line(f"{'#':>3} {'camera_clock':20} {'dur':>7} {'size':>7} {'res':10} status")
for n,c in enumerate(clips,1):
    c['n']=n; name=f"{n:02d}_{c['created']}_{int(c['dur'])}s"
    if n<START_N or (ONLY and n not in ONLY): continue
    t0=time.time()
    ms=deint.samples_of(c['moov']); N=(c['mend']+CL-1)//CL
    hh=deint.find_head2(dev,ftyps,ms,N,c['lrv'])
    headless=False
    if hh is None:
        # head overwritten: start right after the previous index on the card and resync
        prev=[r['moff'] for r in rows if r['moff']<c['moff']]
        head=((max(prev)+len([r for r in rows if r['moff']==max(prev)][0]['moov']))//CL+1)*CL if prev else 0
        headless=True
    else: head=hh[0]
    m,st=deint.build_map3(dev,head,c['moov'],c['lrv'],log=lambda *a:None)
    if headless: st['headless']=True
    ok = st['placed']==st['needed'] and not st.get('headless')
    json.dump({'start':head,'map':m,'stats':st,'moff':c['moff']},open(f"{HERE}/maps/{TAG}_{c['moff']}.json",'w'))
    status=f"mapped ok (lrv={st['lrv']} garbage={st['garbage']})" if ok else f"PARTIAL placed={st['placed']}/{st['needed']} garbage={st['garbage']} resync={st['resync']}{' headless' if st.get('headless') else ''}"
    # stream moov-first to ffmpeg for thumbnails
    mend_eff=min(c['mend'],len(m)*CL)
    if not ok: status+=f' (preview covers first {mend_eff/1e9:.2f}G)'
    rd=deint.file_reader2(dev,head,m,mend_eff)
    moov=bytearray(c['moov']); mdat_size=c['mend']-28; ext=mdat_size+8>=2**32; delta=len(moov)+(8 if ext else 0)
    for tag in (b'stco',b'co64'):
        p=0
        while True:
            j=moov.find(tag,p)
            if j<0: break
            cnt=struct.unpack('>I',moov[j+8:j+12])[0]; w=4 if tag==b'stco' else 8; fmt='>I' if w==4 else '>Q'
            for k in range(cnt):
                o=j+12+k*w; moov[o:o+w]=struct.pack(fmt,struct.unpack(fmt,moov[o:o+w])[0]+delta)
            p=j+4
    mdat_hdr=struct.pack('>I4sQ',1,b'mdat',mdat_size+8) if ext else struct.pack('>I4s',mdat_size,b'mdat')
    iv=max(1,int(c['dur']/40)); tiles=max(1,min(48,int(c['dur']/iv)+1)); cols=8; rows_=(tiles+cols-1)//cols
    cmd=['ffmpeg','-v','error','-y','-err_detect','ignore_err','-hwaccel','videotoolbox','-skip_frame','nokey','-i','pipe:0','-vf',f"fps=1/{iv},scale=320:-1,tile={cols}x{rows_}",'-frames:v','1',f"{OUT}/{name}.jpg"]
    errf=open(f'{HERE}/ff_err.txt','w+'); p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=errf)
    try:
        hdr=rd(0,28) if (m and m[0] is not None) else b'\x00\x00\x00\x14ftypmp41\x00\x00\x00\x00mp41\x00\x00\x00\x08free'
        p.stdin.write(hdr); p.stdin.write(bytes(moov)); p.stdin.write(mdat_hdr)
        f=36
        while f<mend_eff:
            nb=min(32*CL,mend_eff-f); p.stdin.write(rd(f,nb)); f+=nb
        p.stdin.close()
    except BrokenPipeError: pass
    rc=p.wait(); errf.seek(0); err=errf.read(); errf.close()
    nerr=len([l for l in err.splitlines() if 'PPS changed' not in l and 'repeated' not in l])
    line(f"{n:3} {c['created']:20} {c['dur']:6.1f}s {c['mend']/1e9:6.2f}G {c['w']}x{c['h']:<5} {status}; preview {'ok' if rc==0 else 'rc='+str(rc)} decode_errs={nerr} ({time.time()-t0:.0f}s)")
line("done")
