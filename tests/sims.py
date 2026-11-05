import sys, os, time, io, socket, threading, subprocess, unicodedata, pathlib, http.client, uuid
from concurrent.futures import ThreadPoolExecutor
import urllib.request
APP, INBOX, PORT = sys.argv[1], pathlib.Path(sys.argv[2]), int(sys.argv[3])
BASE = f"http://127.0.0.1:{PORT}"
H = {"X-Token": "secret123"}
results = []
def rec(name, ok, detail=""):
    results.append((name, ok)); print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

def multipart(fields=(), files=()):
    b = uuid.uuid4().hex; out = io.BytesIO()
    for k, v in fields:
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode()); out.write(v.encode()); out.write(b"\r\n")
    for k, fn, data in files:
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode("utf-8")); out.write(data); out.write(b"\r\n")
    out.write(f"--{b}--\r\n".encode()); return out.getvalue(), f"multipart/form-data; boundary={b}"

def post(fields=(), files=(), timeout=30):
    body, ct = multipart(fields, files)
    req = urllib.request.Request(BASE + "/drop", data=body, headers={**H, "Content-Type": ct}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:80]
    except Exception as e: return -1, repr(e)

def start(env=None):
    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "server.py"), APP, str(INBOX), str(PORT)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, **(env or {})})
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", PORT), 0.2).close(); return p
        except OSError: time.sleep(0.1)
    return p

srv = start()

# 1. 10 parallel uploads, same name -> 10 distinct files, none empty
with ThreadPoolExecutor(10) as ex:
    rs = list(ex.map(lambda i: post(files=[("item", "race.jpg", f"payload{i}".encode() * 1000)]), range(10)))
files = [p for p in INBOX.iterdir() if p.name.startswith("race")]
sizes = {p.stat().st_size for p in files}
rec("10 parallel same-name uploads", len(files) == 10 and 0 not in sizes and all(s == 200 for s, _ in rs), f"{len(files)} files, sizes={sorted(sizes)[:3]}")

# 2. client aborts mid-upload -> no junk file left
s = socket.create_connection(("127.0.0.1", PORT))
body, ct = multipart(files=[("item", "aborted.mov", b"x" * 5_000_000)])
s.sendall(f"POST /drop HTTP/1.1\r\nHost: x\r\nX-Token: secret123\r\nContent-Type: {ct}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body[:1_000_000])
s.close(); time.sleep(1)
rec("aborted upload leaves no junk", not any(p.name.startswith("aborted") for p in INBOX.iterdir()))

# 3. disk full mid-save -> error returned AND no half-written file left
st, txt = post(files=[("item", "DISKFULL.mov", b"y" * 50000)])
left = [p for p in INBOX.iterdir() if p.name.startswith("DISKFULL")]
rec("disk full: error reported to phone", st >= 400, f"status={st}")
rec("disk full: no half-written file", not left, f"left={[p.name for p in left]}")

# 4. empty request -> phone should be told nothing arrived
st, txt = post(fields=[("text", "   ")])
rec("empty/blank request flagged (not 'ok')", st >= 400, f"status={st} body={txt!r}")

# 5. 60 files in one request
st, txt = post(files=[("item", f"batch{i}.jpg", b"z" * 2000) for i in range(60)])
n = len([p for p in INBOX.iterdir() if p.name.startswith("batch")])
rec("60 files in one request", st == 200 and n == 60, f"saved {n}")

# 6. iOS-style decomposed (NFD) accented name -> stored as normal (NFC)
nfd = unicodedata.normalize("NFD", "Résumé été.pdf")
post(files=[("item", nfd, b"%PDF")])
names = [p.name for p in INBOX.iterdir() if "sum" in p.name]
rec("NFD filename normalised to NFC", any(n == unicodedata.normalize("NFC", n) and "Résumé" in n for n in names), f"{[ascii(n) for n in names]}")

# 7. second instance on same port -> exits cleanly, first keeps working
p2 = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "server.py"), APP, str(INBOX), str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try: out = p2.communicate(timeout=8)[0].decode()
except subprocess.TimeoutExpired: p2.kill(); out = "STILL RUNNING (hung)"
st, _ = post(fields=[("text", "still alive")])
rec("second instance doesn't hang/break first", st == 200 and "STILL RUNNING" not in out, f"2nd exit={p2.returncode} msg={out.strip().splitlines()[-1][:90] if out.strip() else ''!r}")

# 8. handler crash is written to a log file (pythonw has no console)
logs = list(INBOX.glob("*.log")) + list(pathlib.Path(APP).glob("*.log"))
rec("errors written to a log file", any("No space left" in l.read_text(errors="ignore") for l in logs), f"logs={[l.name for l in logs]}")

srv.terminate(); srv.wait()

# 9. clipboard locked for ~0.6 s -> still lands
srv = start({"CLIP_FAIL": "3"})
st, _ = post(fields=[("text", "retry me")])
clip = urllib.request.urlopen(urllib.request.Request(BASE + "/pull", headers=H)).read().decode()
rec("clipboard briefly locked -> retried", clip == "retry me", f"clip={clip!r}")
srv.terminate(); srv.wait()

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
