#!/usr/bin/env python3
"""GoPro Recover - local web app. Run: python3 app.py  (or double-click 'GoPro Recover.command')."""
import os, sys, json, threading, webbrowser, subprocess, time, traceback, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core

WORK_ROOT = os.path.expanduser("~/GoPro Recover")
STATE = dict(disk=None, work=None, job=None)   # job: dict(kind, progress, msg, done, error, result)
if os.environ.get("GOPRO_WORK"):   # developer: preload a work folder without a card
    STATE["work"] = os.environ["GOPRO_WORK"]
    STATE["disk"] = dict(device="/dev/disk4", raw=os.environ.get("GOPRO_RAW", "/dev/rdisk4"), size=0, name="preloaded")
LOCK = threading.Lock()


def run_job(kind, fn):
    with LOCK:
        if STATE["job"] and not STATE["job"]["done"]:
            return False
        STATE["job"] = dict(kind=kind, progress=0.0, msg="starting", done=False, error=None, result=None, stop=False)
    job = STATE["job"]

    def prog(f, msg):
        job["progress"], job["msg"] = f, msg

    def runner():
        try:
            job["result"] = fn(prog)
        except FileNotFoundError:
            job["error"] = "The card isn't connected. Reinsert it (and click Refresh on the Card step), then try again."
        except PermissionError:
            job["error"] = "No permission to read the card. Go back to the Card step and click 'Use this card' to grant access."
        except Exception as e:
            job["error"] = f"{e.__class__.__name__}: {e}"
            traceback.print_exc()
        job["done"] = True
    threading.Thread(target=runner, daemon=True).start()
    return True


def work_for(disk):
    facts = core.card_facts(disk["raw"])
    tag = f"{disk['name'] or 'card'}_{facts['total']//1_000_000}MB".replace("/", "_").replace(" ", "_")
    w = os.path.join(WORK_ROOT, tag)
    os.makedirs(w, exist_ok=True)
    return w, facts


def choose_folder():
    if sys.platform == "darwin":
        r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Save recovered files to:")'],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    return os.path.expanduser("~/Desktop")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def _file(self, path, ctype):
        if not path or not os.path.exists(path):
            self.send_response(404); self.end_headers(); return
        data = open(path, "rb").read()
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query); p = u.path
        if p == "/":
            return self._file(os.path.join(HERE, "ui", "index.html"), "text/html; charset=utf-8")
        if p == "/api/disks":
            return self._json(dict(disks=core.list_disks(), ffmpeg=bool(core.find_ffmpeg()), selected=STATE["disk"]))
        if p == "/api/status":
            j = STATE["job"]
            scan = core.load_scan(STATE["work"]) if STATE["work"] else None
            return self._json(dict(job=j and {k: v for k, v in j.items() if k != "stop"}, disk=STATE["disk"],
                                   scanned=bool(scan), scan=scan and dict(when=scan["when"], videos=len(scan["moovs"]), photos=len(scan["jpegs"]), facts=scan["facts"])))
        if p == "/api/clips":
            if not STATE["work"]:
                return self._json(dict(clips=[]))
            rows = core.clips(STATE["work"])
            maps = os.path.join(STATE["work"], "maps")
            for r in rows:
                mp = os.path.join(maps, f"{r['moff']}.json")
                r["preview"] = os.path.exists(os.path.join(STATE["work"], "previews", f"clip_{r['moff']}.jpg"))
                if os.path.exists(mp):
                    st = json.load(open(mp))["stats"]
                    r["intact"] = round(100.0 * st["placed"] / max(1, st["needed"]), 1)
                else:
                    r["intact"] = None
            return self._json(dict(clips=rows))
        if p == "/api/photos":
            if not STATE["work"]:
                return self._json(dict(photos=[]))
            ph = core.photos(STATE["work"])
            for x in ph:
                x["thumb"] = os.path.exists(os.path.join(STATE["work"], "previews", f"photo_{x['offset']}.jpg"))
            return self._json(dict(photos=ph))
        if p.startswith("/preview/clip/"):
            moff = p.rsplit("/", 1)[1]
            return self._file(os.path.join(STATE["work"], "previews", f"clip_{moff}.jpg"), "image/jpeg")
        if p.startswith("/preview/photo/"):
            off = p.rsplit("/", 1)[1]
            return self._file(os.path.join(STATE["work"], "previews", f"photo_{off}.jpg"), "image/jpeg")
        self.send_response(404); self.end_headers()

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        body = self._body()
        try:
            if p == "/api/select":
                disk = body["disk"]
                core.ensure_readable(disk)
                STATE["disk"] = disk
                STATE["work"], facts = work_for(disk)
                return self._json(dict(ok=True, work=STATE["work"], facts=facts))
            if p == "/api/scan":
                raw, work = STATE["disk"]["raw"], STATE["work"]
                core.ensure_readable(STATE["disk"])
                ok = run_job("scan", lambda prog: core.scan(raw, work, prog, stop=lambda: STATE["job"]["stop"]))
                return self._json(dict(ok=ok))
            if p == "/api/stop":
                if STATE["job"]:
                    STATE["job"]["stop"] = True
                return self._json(dict(ok=True))
            if p == "/api/preview":
                raw, work = STATE["disk"]["raw"], STATE["work"]
                ids = set(body.get("ids", []))
                rows = [r for r in core.clips(work) if r["id"] in ids]

                def fn(prog):
                    outs = []
                    for i, r in enumerate(rows):
                        prog(i / len(rows), f"clip {r['id']} of {len(rows)}: mapping")
                        outs.append(core.preview_clip(raw, work, r, lambda f, m: prog((i + f) / len(rows), f"clip {r['id']}: {m}")))
                    return outs
                return self._json(dict(ok=run_job("preview", fn)))
            if p == "/api/recover":
                raw, work = STATE["disk"]["raw"], STATE["work"]
                ids = set(body.get("ids", []))
                dest = body.get("dest") or choose_folder()
                if not dest:
                    return self._json(dict(ok=False, error="no folder chosen"))
                rows = [r for r in core.clips(work) if r["id"] in ids]

                def fn(prog):
                    outs = []
                    for i, r in enumerate(rows):
                        name = f"{r['created'] or 'clip'}_{int(r['duration'])}s_{r['w']}x{r['h']}_{r['id']:03d}.mp4"
                        outs.append(core.extract_clip(raw, work, r, os.path.join(dest, name),
                                                      lambda f, m: prog((i + f) / len(rows), f"clip {r['id']}: {m}")))
                    return outs
                return self._json(dict(ok=run_job("recover", fn), dest=dest))
            if p == "/api/photo_thumbs":
                raw, work = STATE["disk"]["raw"], STATE["work"]
                ids = set(body.get("ids", []))
                ph = [x for x in core.photos(work) if x["id"] in ids]

                def fn(prog):
                    for i, x in enumerate(ph):
                        prog(i / max(1, len(ph)), f"photo {x['id']}")
                        core.photo_thumb(raw, work, x)
                    return True
                return self._json(dict(ok=run_job("thumbs", fn)))
            if p == "/api/recover_photos":
                raw, work = STATE["disk"]["raw"], STATE["work"]
                ids = set(body.get("ids", []))
                dest = body.get("dest") or choose_folder()
                if not dest:
                    return self._json(dict(ok=False, error="no folder chosen"))
                ph = [x for x in core.photos(work) if x["id"] in ids]

                def fn(prog):
                    outs = []
                    for i, x in enumerate(ph):
                        prog(i / max(1, len(ph)), f"photo {x['id']}")
                        try:
                            outs.append(core.extract_photo(raw, work, x, os.path.join(dest, f"photo_{x['id']:04d}.jpg")))
                        except Exception as e:
                            outs.append(f"photo {x['id']}: {e}")
                    return outs
                return self._json(dict(ok=run_job("recover", fn), dest=dest))
            if p == "/api/choose_folder":
                return self._json(dict(path=choose_folder()))
            if p == "/api/open":
                subprocess.run(["open", body["path"]] if sys.platform == "darwin" else ["xdg-open", body["path"]])
                return self._json(dict(ok=True))
        except Exception as e:
            traceback.print_exc()
            return self._json(dict(ok=False, error=f"{e.__class__.__name__}: {e}"), 500)
        self.send_response(404); self.end_headers()


def main():
    port = int(os.environ.get("PORT", "8765"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}/"
    print(f"GoPro Recover running at {url}  (close this window to quit)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
