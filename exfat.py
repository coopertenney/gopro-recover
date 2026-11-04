import struct,sys
SEC=512
class ExFat:
    def __init__(s,path,part_sector=2048):
        s.fh=open(path,'rb',buffering=0); s.base=part_sector*SEC
        bs=s.rd(s.base,512); assert bs[3:11]==b'EXFAT   ', bs[3:11]
        s.bps=1<<bs[108]; s.spc=1<<bs[109]; s.cl=s.bps*s.spc
        s.fat_off=struct.unpack('<I',bs[80:84])[0]; s.fat_len=struct.unpack('<I',bs[84:88])[0]
        s.heap=struct.unpack('<I',bs[88:92])[0]; s.ncl=struct.unpack('<I',bs[92:96])[0]; s.root=struct.unpack('<I',bs[96:100])[0]
    def rd(s,off,n):
        a=off-(off%SEC); n2=((off+n-a)+SEC-1)//SEC*SEC
        s.fh.seek(a); return s.fh.read(n2)[off-a:off-a+n]
    def cl_off(s,c): return s.base+(s.heap*s.bps)+(c-2)*s.cl
    def fat(s,c):
        return struct.unpack('<I',s.rd(s.base+s.fat_off*s.bps+c*4,4))[0]
    def chain(s,first,size,nofat,maxn=10**6):
        n=(size+s.cl-1)//s.cl; out=[]
        c=first
        for i in range(n):
            out.append(c)
            if nofat: c+=1
            else:
                c=s.fat(c)
                if c>=0xFFFFFFF7 or c<2: break
        return out
    def listdir(s,first,size=None,nofat=False):
        """yield dict(name,size,first,nofat,deleted,isdir)"""
        clusters=s.chain(first,size,nofat) if size else None
        c=first; ents=[]
        while True:
            data=s.rd(s.cl_off(c),s.cl)
            for i in range(0,s.cl,32):
                e=data[i:i+32]
                if e[0]==0: return ents
                ents.append(e)
            if nofat: c+=1
            else:
                c=s.fat(c)
                if c>=0xFFFFFFF7 or c<2: return ents
    def parse(s,ents):
        out=[]; i=0
        while i<len(ents):
            e=ents[i]; t=e[0]&0x7f; inuse=bool(e[0]&0x80)
            if t==0x05:  # file dir entry
                nsec=e[1]; attr=struct.unpack('<H',e[4:6])[0]
                st=ents[i+1] if i+1<len(ents) else None
                name=''
                if st is not None and (st[0]&0x7f)==0x40:
                    flags=st[1]; nlen=st[3]; vlen=struct.unpack('<Q',st[8:16])[0]; first=struct.unpack('<I',st[20:24])[0]; dlen=struct.unpack('<Q',st[24:32])[0]
                    for j in range(2,nsec+1):
                        ne=ents[i+j] if i+j<len(ents) else None
                        if ne is not None and (ne[0]&0x7f)==0x41: name+=ne[2:32].decode('utf-16le','replace')
                    name=name[:nlen]
                    out.append(dict(name=name,size=dlen,first=first,nofat=bool(flags&2),deleted=not inuse,isdir=bool(attr&0x10)))
                i+=nsec+1
            else: i+=1
        return out
if __name__=='__main__':
    fs=ExFat(sys.argv[1], int(sys.argv[2]) if len(sys.argv)>2 else 2048)
    print(f"cluster={fs.cl} root={fs.root} heap_sector={fs.heap} fat_off={fs.fat_off}")
    def walk(first,path,depth=0):
        for d in fs.parse(fs.listdir(first)):
            flag='DEL' if d['deleted'] else '   '
            print(f"{'  '*depth}{flag} {'[D]' if d['isdir'] else '   '} {d['name']:28} {d['size']/1e6:9.1f}MB first={d['first']} contiguous={d['nofat']}")
            if d['isdir'] and depth<3 and not d['name'].startswith('.'):
                walk(d['first'],path+'/'+d['name'],depth+1)
    walk(fs.root,'')
