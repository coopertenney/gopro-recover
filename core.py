"""Core recovery engine used by the app (and reusable from Python).

Everything reads the card read-only. State for a card lives in a work folder:
  <work>/moov/moov_<offset>.bin   recovered MP4 indexes
  <work>/scan.json                 headers, photos, card facts
  <work>/maps/<moov>.json          block maps built on demand
  <work>/previews/*.jpg            contact sheets / photo thumbnails
"""
import os, re, sys, json, struct, time, subprocess, plistlib, datetime, shutil, threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mp4lib, deint

SEC = 512
CAMERA_NAMES = {"HD5": "HERO5 Black", "HD6": "HERO6 Black", "HD7": "HERO7 Black", "HD8": "HERO8 Black",
                "HD9": "HERO9 Black", "H21": "HERO10 Black", "H22": "HERO11 Black", "H23": "HERO12 Black",
                "H24": "HERO13 Black", "H25": "HERO (2025)"}
FFMPEG = None


def find_ffmpeg():
    global FFMPEG
    if FFMPEG:
        return FFMPEG
    for cand in [os.path.join(HERE, "bin", "ffmpeg"), shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if cand and os.path.exists(cand):
            FFMPEG = cand
            return cand
    return None


# ---------------------------------------------------------------- disks / access
def list_disks():
    """External physical disks on macOS (diskutil) or Linux (lsblk)."""
    out = []
    if sys.platform == "darwin":
        try:
            pl = plistlib.loads(subprocess.run(["diskutil", "list", "-plist", "external", "physical"],
                                               capture_output=True).stdout)
        except Exception:
            return out
        for d in pl.get("AllDisksAndPartitions", []):
            dev = "/dev/" + d["DeviceIdentifier"]
            name = ", ".join(p.get("VolumeName") or p.get("Content", "") for p in d.get("Partitions", [])) or d.get("Content", "")
            out.append(dict(device=dev, raw="/dev/r" + d["DeviceIdentifier"], size=d.get("Size", 0), name=name))
    else:
        try:
            js = json.loads(subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,RM,LABEL"], capture_output=True).stdout)
            for d in js.get("blockdevices", []):
                if d.get("type") == "disk" and d.get("rm") in (True, "1", 1):
                    out.append(dict(device="/dev/" + d["name"], raw="/dev/" + d["name"], size=int(d["size"]),
                                    name=", ".join(c.get("label") or "" for c in d.get("children", []))))
        except Exception:
            pass
    return out


def ensure_readable(disk):
    """Make the raw device readable. On macOS asks for the admin password through the system dialog."""
    raw = disk["raw"]
    try:
        with open(raw, "rb", buffering=0) as fh:
            fh.read(512)
        return True
    except PermissionError:
        pass
    if sys.platform == "darwin":
        subprocess.run(["diskutil", "unmountDisk", disk["device"]], capture_output=True)
        script = f'do shell script "chmod 644 {raw} {disk["device"]}" with administrator privileges'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode != 0:
            raise PermissionError(r.stderr.strip() or "admin access refused")
    else:
        raise PermissionError(f"run: sudo chmod 644 {raw}")
    with open(raw, "rb", buffering=0) as fh:
        fh.read(512)
    return True


def card_facts(raw):
    """Partition start, filesystem, cluster size, total bytes."""
    dev = deint.Dev(raw)
    mbr = dev.rd(0, 512)
    part = 0
    for i in range(4):
        e = mbr[446 + 16 * i:462 + 16 * i]
        if e[4]:
            part = struct.unpack("<I", e[8:12])[0]
            break
    bs = dev.rd(part * SEC, 512)
    fs, cluster = "unknown", 131072
    if bs[3:11] == b"EXFAT   ":
        fs, cluster = "exFAT", (1 << bs[108]) * (1 << bs[109])
    elif bs[82:87] == b"FAT32":
        fs, cluster = "FAT32", bs[11] | (bs[12] << 8)
        cluster *= bs[13]
    total = 0
    if sys.platform == "darwin":
        try:
            pl = plistlib.loads(subprocess.run(["diskutil", "info", "-plist", raw.replace("/dev/r", "/dev/")], capture_output=True).stdout)
            total = pl.get("TotalSize", 0) or pl.get("Size", 0)
        except Exception:
            pass
    if not total:
        try:
            with open(raw, "rb") as fh:
                fh.seek(0, 2); total = fh.tell()
        except Exception:
            total = 0
    return dict(partition_start=part, fs=fs, cluster=cluster, total=total)


# ---------------------------------------------------------------- scan
def scan(raw, work, progress=lambda f, msg: None, stop=lambda: False):
    """One sequential pass over the card. Finds MP4 indexes, file headers and JPEG photos."""
    facts = card_facts(raw)
    total = facts["total"] or 1
    os.makedirs(os.path.join(work, "moov"), exist_ok=True)
    dev = deint.Dev(raw)
    CH, OV = 32 * 1024 * 1024, 64
    fh = dev.fh
    fh.seek(0)
    off, prev = 0, b""
    ftyps, moovs, jpegs = [], [], []
    t0 = time.time()
    while True:
        if stop():
            break
        b = fh.read(CH)
        if not b:
            break
        buf = prev + b
        base = off - len(prev)
        p = 0
        while True:
            j = buf.find(b"ftyp", p)
            if j < 0:
                break
            if j >= 4 and (base + j - 4) % SEC == 0:
                ftyps.append(base + j - 4)
            p = j + 4
        p = 0
        while True:
            j = buf.find(b"moov", p)
            if j < 0:
                break
            if j >= 4 and buf[j + 8:j + 12] == b"mvhd":
                size = struct.unpack(">I", buf[j - 4:j])[0]
                if 100 < size < 300 * 1024 * 1024:
                    moff = base + j - 4
                    m = dev.rd(moff, size)
                    with open(os.path.join(work, "moov", f"moov_{moff}.bin"), "wb") as o:
                        o.write(m)
                    moovs.append(moff)
            p = j + 4
        p = 0
        while True:
            j = buf.find(b"\xff\xd8\xff", p)
            if j < 0:
                break
            if (base + j) % SEC == 0 and buf[j + 3] in (0xe1, 0xe0, 0xdb):
                jpegs.append(base + j)
            p = j + 3
        prev = buf[-OV:]
        off += len(b)
        if (off // CH) % 8 == 0:
            rate = off / 1e6 / max(0.1, time.time() - t0)
            progress(min(0.99, off / total), f"{off/1e9:.1f} of {total/1e9:.1f} GB at {rate:.0f} MB/s, {len(moovs)} videos, {len(jpegs)} photos")
    scanjs = dict(raw=raw, facts=facts, ftyps=sorted(set(ftyps)), moovs=sorted(set(moovs)), jpegs=sorted(set(jpegs)),
                  scanned=off, when=datetime.datetime.now().isoformat(timespec="seconds"))
    with open(os.path.join(work, "scan.json"), "w") as o:
        json.dump(scanjs, o)
    progress(0.99, "cataloguing videos")
    clips(work, progress=lambda f, m: progress(0.99, m))
    progress(1.0, f"done: {len(moovs)} video indexes, {len(jpegs)} photo headers, {len(ftyps)} file headers")
    return scanjs


def load_scan(work):
    p = os.path.join(work, "scan.json")
    return json.load(open(p)) if os.path.exists(p) else None


# ---------------------------------------------------------------- clip catalogue
def _codec(mm):
    for ts, te in mp4lib.find(mm, [b"trak"]):
        hd = mp4lib.find(mm, [b"mdia", b"hdlr"], ts, te)
        if hd and mm[hd[0][0] + 8:hd[0][0] + 12] == b"vide":
            sd = mp4lib.find(mm, [b"mdia", b"minf", b"stbl", b"stsd"], ts, te)
            if sd:
                return mm[sd[0][0] + 12:sd[0][0] + 16].decode("latin1")
    return "?"


def clips(work, progress=lambda f, msg: None):
    """All video recordings found, in on-card order. LRV sidecars are attached to their main clip. Cached in clips.json."""
    D = os.path.join(work, "moov")
    if not os.path.isdir(D):
        return []
    cache = os.path.join(work, "clips.json")
    scanp = os.path.join(work, "scan.json")
    if os.path.exists(cache) and os.path.exists(scanp) and os.path.getmtime(cache) >= os.path.getmtime(scanp):
        return json.load(open(cache))
    rows = []
    names = os.listdir(D)
    for i, fn in enumerate(names):
        if i % 10 == 0:
            progress(i / max(1, len(names)), f"cataloguing videos {i}/{len(names)}")
        moff = int(re.findall(r"\d+", fn)[0])
        m = open(os.path.join(D, fn), "rb").read()
        mm = m[8:] if m[4:8] == b"moov" else m
        try:
            w, h = mp4lib.video_dims(m)
            created, dur = mp4lib.mvhd_info(m)
            tracks = mp4lib.parse_tracks(m)
            mend = mp4lib.mdat_end(tracks)
        except Exception:
            continue
        k = mm.find(b"FIRM")
        firm = mm[k + 4:k + 14].decode("latin1", "replace").strip("\x00 ") if k > 0 else ""
        cam = CAMERA_NAMES.get(firm[:3], firm[:3] or "unknown")
        codec = _codec(mm)
        fps = None
        rows.append(dict(moff=moff, w=w, h=h, created=created or "", duration=round(dur or 0, 1), size=mend + len(m),
                         firmware=firm, camera=cam, codec={"hvc1": "H.265", "hev1": "H.265", "avc1": "H.264"}.get(codec, codec),
                         mend=mend, moov_len=len(m)))
    rows.sort(key=lambda r: r["moff"])
    # pair sidecars: a small-resolution index shortly after a full one with the same duration
    out = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r.get("kind") == "paired":
            i += 1; continue
        if r["w"] == 0:
            r["kind"] = "unknown"; out.append(r); i += 1; continue
        if r["w"] < 1000:
            # orphan sidecar (main index missing) -> still list it, flagged
            r["kind"] = "sidecar"; out.append(r); i += 1; continue
        r["kind"] = "video"; r["lrv"] = None
        for j in range(i + 1, min(i + 4, len(rows))):
            s = rows[j]
            if 0 < s["w"] < 1000 and abs(s["duration"] - r["duration"]) < 1.5:
                r["lrv"] = s["moff"]; rows[j]["kind"] = "paired"; break
        out.append(r); i += 1
    out = [r for r in out if r.get("kind") != "paired"]
    for n, r in enumerate(out, 1):
        r["id"] = n
    with open(cache, "w") as o:
        json.dump(out, o)
    return out


def photos(work):
    sc = load_scan(work)
    return [dict(id=n, offset=o) for n, o in enumerate(sc["jpegs"], 1)] if sc else []


# ---------------------------------------------------------------- mapping / preview / extract
_map_lock = threading.Lock()


def _set_cluster(work):
    sc = load_scan(work)
    deint.CL = sc["facts"]["cluster"] if sc else 131072


def build_map(raw, work, clip, progress=lambda f, msg: None):
    """Find the clip's first block and walk the card to map every block. Cached."""
    mp = os.path.join(work, "maps", f"{clip['moff']}.json")
    if os.path.exists(mp):
        return json.load(open(mp))
    os.makedirs(os.path.dirname(mp), exist_ok=True)
    _set_cluster(work)
    CL = deint.CL
    sc = load_scan(work)
    dev = deint.Dev(raw)
    moov = open(os.path.join(work, "moov", f"moov_{clip['moff']}.bin"), "rb").read()
    lrv = open(os.path.join(work, "moov", f"moov_{clip['lrv']}.bin"), "rb").read() if clip.get("lrv") else None
    ms = deint.samples_of(moov)
    N = (max(o + z for o, z, _ in ms) + CL - 1) // CL
    progress(0.05, "locating first block")
    hh = deint.find_head2(dev, sc["ftyps"], ms, N, lrv)
    headless = hh is None
    if headless:
        prev = [m for m in sc["moovs"] if m < clip["moff"]]
        start = ((max(prev) + 300000) // CL + 1) * CL if prev else 0
    else:
        start = hh[0]
    progress(0.15, "walking blocks")
    mapping, st = deint.build_map3(dev, start, moov, lrv, log=None)
    st["headless"] = headless
    res = dict(start=start, map=mapping, stats=st, moff=clip["moff"])
    with open(mp, "w") as o:
        json.dump(res, o)
    return res


def _patched_moov_first(moov, mend):
    moov = bytearray(moov)
    mdat_size = mend - 28
    ext = mdat_size + 8 >= 2 ** 32
    delta = len(moov) + (8 if ext else 0)
    for tag in (b"stco", b"co64"):
        p = 0
        while True:
            j = moov.find(tag, p)
            if j < 0:
                break
            cnt = struct.unpack(">I", moov[j + 8:j + 12])[0]
            w = 4 if tag == b"stco" else 8
            fmt = ">I" if w == 4 else ">Q"
            for k in range(cnt):
                o = j + 12 + k * w
                moov[o:o + w] = struct.pack(fmt, struct.unpack(fmt, moov[o:o + w])[0] + delta)
            p = j + 4
    hdr = struct.pack(">I4sQ", 1, b"mdat", mdat_size + 8) if ext else struct.pack(">I4s", mdat_size, b"mdat")
    return bytes(moov), hdr


GOPRO_HEAD = b"\x00\x00\x00\x14ftypmp41\x00\x00\x00\x00mp41\x00\x00\x00\x08free"


def preview_clip(raw, work, clip, progress=lambda f, msg: None):
    """Contact sheet for one clip -> path to jpg."""
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg not found")
    out_dir = os.path.join(work, "previews"); os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"clip_{clip['moff']}.jpg")
    if os.path.exists(out):
        return out
    mp = build_map(raw, work, clip, progress)
    _set_cluster(work); CL = deint.CL
    dev = deint.Dev(raw)
    moov = open(os.path.join(work, "moov", f"moov_{clip['moff']}.bin"), "rb").read()
    m = mp["map"]
    mend_eff = min(clip["mend"], len(m) * CL)
    rd = deint.file_reader2(dev, mp["start"], m, mend_eff)
    pm, mdat_hdr = _patched_moov_first(moov, clip["mend"])
    dur = max(1.0, clip["duration"])
    iv = max(1, int(dur / 40)); tiles = max(1, min(48, int(dur / iv) + 1)); cols = 8; rows_ = (tiles + cols - 1) // cols
    cmd = [ff, "-v", "error", "-y", "-err_detect", "ignore_err", "-skip_frame", "nokey", "-i", "pipe:0",
           "-vf", f"fps=1/{iv},scale=320:-1,tile={cols}x{rows_}", "-frames:v", "1", out]
    if sys.platform == "darwin":
        cmd[1:1] = ["-hwaccel", "videotoolbox"]
    errf = open(out + ".log", "w")
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf)
    progress(0.3, "decoding frames")
    try:
        hdr = rd(0, 28) if m and m[0] is not None else GOPRO_HEAD
        p.stdin.write(hdr); p.stdin.write(pm); p.stdin.write(mdat_hdr)
        f = 36
        while f < mend_eff:
            n = min(32 * CL, mend_eff - f); p.stdin.write(rd(f, n)); f += n
            progress(0.3 + 0.7 * f / mend_eff, "decoding frames")
        p.stdin.close()
    except BrokenPipeError:
        pass
    p.wait(); errf.close()
    return out if os.path.exists(out) else None


def extract_clip(raw, work, clip, dest, progress=lambda f, msg: None):
    mp = build_map(raw, work, clip, progress)
    _set_cluster(work); CL = deint.CL
    dev = deint.Dev(raw)
    moov = open(os.path.join(work, "moov", f"moov_{clip['moff']}.bin"), "rb").read()
    m = mp["map"]
    rd = deint.file_reader2(dev, mp["start"], m, clip["mend"])
    hdr = rd(0, 28) if m and m[0] is not None else GOPRO_HEAD
    with open(dest, "wb") as o:
        o.write(hdr); o.write(struct.pack(">I4s", clip["mend"] - 28, b"mdat"))
        f = 36
        while f < clip["mend"]:
            n = min(64 * CL, clip["mend"] - f); o.write(rd(f, n)); f += n
            progress(f / clip["mend"], "writing")
        o.write(moov)
    return dest


# ---------------------------------------------------------------- photos
def _jpeg_length(dev, off, limit=60 * 1024 * 1024):
    """Walk JPEG segments to SOS, then find the real EOI. Returns byte length or None."""
    b = dev.rd(off, 65536)
    if b[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(b):
        if b[i] != 0xff:
            return None
        marker = b[i + 1]
        if marker == 0xda:  # SOS
            break
        if marker in (0xd8, 0x01) or 0xd0 <= marker <= 0xd7:
            i += 2; continue
        L = struct.unpack(">H", b[i + 2:i + 4])[0]
        i += 2 + L
        if i > len(b) - 4:
            b = dev.rd(off, min(limit, len(b) * 4))
    pos = i
    CH = 4 * 1024 * 1024
    while pos < limit:
        chunk = dev.rd(off + pos, CH + 2)
        if not chunk:
            return None
        k = 0
        while True:
            k = chunk.find(b"\xff\xd9", k)
            if k < 0:
                break
            return pos + k + 2
        pos += CH
    return None


def photo_info(raw, work, ph):
    dev = deint.Dev(raw)
    b = dev.rd(ph["offset"], 2048)
    n = _jpeg_length(dev, ph["offset"])
    # dimensions from SOF marker
    w = h = None
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xff: break
        mk = b[i + 1]
        if mk in (0xc0, 0xc1, 0xc2):
            h, w = struct.unpack(">HH", b[i + 5:i + 9]); break
        if mk == 0xd8 or 0xd0 <= mk <= 0xd7: i += 2; continue
        L = struct.unpack(">H", b[i + 2:i + 4])[0]; i += 2 + L
    # EXIF date
    m = re.search(rb"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", b)
    date = m.group(0).decode() if m else ""
    return dict(size=n, w=w, h=h, date=date)


def photo_thumb(raw, work, ph):
    ff = find_ffmpeg()
    out_dir = os.path.join(work, "previews"); os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"photo_{ph['offset']}.jpg")
    if os.path.exists(out):
        return out
    dev = deint.Dev(raw)
    n = _jpeg_length(dev, ph["offset"]) or 2 * 1024 * 1024
    data = dev.rd(ph["offset"], n)
    p = subprocess.run([ff, "-v", "error", "-y", "-i", "pipe:0", "-vf", "scale=320:-1", "-frames:v", "1", out], input=data, capture_output=True)
    return out if os.path.exists(out) else None


def extract_photo(raw, work, ph, dest):
    dev = deint.Dev(raw)
    n = _jpeg_length(dev, ph["offset"])
    if not n:
        raise RuntimeError("could not find end of JPEG")
    with open(dest, "wb") as o:
        o.write(dev.rd(ph["offset"], n))
    return dest
