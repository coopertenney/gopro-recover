import sys,os,re,json,struct
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__))); import deint, mp4lib
DEV,TAG=sys.argv[1],sys.argv[2]; CL=deint.CL; dev=deint.Dev(DEV)
log=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),f"scan_{TAG}.log")).read(); ftyps=sorted(eval(re.search(r"FTYP offsets: (\[.*?\])",log,re.S).group(1)))
D=os.path.expanduser(f'~/Desktop/GoPro_moov_{TAG}'); claimed={}
for fn in sorted(os.listdir(D), key=lambda x:int(re.findall(r'\d+',x)[0])):
    moff=int(re.findall(r'\d+',fn)[0]); m=open(f"{D}/{fn}",'rb').read()
    try:
        w,h=mp4lib.video_dims(m); c,d=mp4lib.mvhd_info(m)
        if w<1900: continue
        ms=deint.samples_of(m); N=(max(o+z for o,z,_ in ms)+CL-1)//CL
        hh=deint.find_head2(dev,ftyps,ms,N,None)
        print(f"moov@{moff} {c} {d:6.1f}s -> head {hh}",flush=True)
        if hh: claimed[hh[0]]=moff
    except Exception as e: print("err",fn,e,flush=True)
print("\nUNCLAIMED ftyps with mdat field > 50MB:")
for p in ftyps:
    h=dev.rd(p,40); mdat=struct.unpack('>I',h[28:32])[0] if h[32:36]==b'mdat' else -1
    if mdat>50e6 and p not in claimed: print(f"  ftyp@{p} mdat_field={mdat/1e6:.1f}MB",flush=True)
json.dump({str(k):v for k,v in claimed.items()},open(f"claimed_{TAG}.json","w"))
print("done")
