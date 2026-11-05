import sys, os, io, types, pathlib, importlib, tempfile, shutil, re, json
from urllib.parse import unquote
APP, TMP = sys.argv[1], sys.argv[2]
# fake clipboard with a Windows-like change counter
state = {"text": "", "seq": 100}
pc = types.ModuleType("pyperclip")
def _copy(t): state["text"] = t; state["seq"] += 1
pc.copy = _copy; pc.paste = lambda: state["text"]; sys.modules["pyperclip"] = pc
os.environ["DROP_TOKEN"] = "secret123"
work = pathlib.Path(tempfile.mkdtemp(dir=TMP))
appdir = work / "app"; appdir.mkdir(parents=True); shutil.copy2(os.path.join(APP, "iphone_drop.py"), appdir)  # receiver only
sys.path.insert(0, str(appdir)); m = importlib.import_module("iphone_drop")
m.clip_seq = lambda: state["seq"]
m.INBOX = work / "Downloads"; m.INBOX.mkdir(); m.OUTBOX = work / "_outbox"
m.OPEN_LINKS = False
import PIL.ImageGrab as IG
from PIL import Image
grab = {"v": None}; IG.grabclipboard = lambda: grab["v"]
m.SYNC.poll()                                   # receiver start: current clipboard = old
c = m.app.test_client(); TS = {"REMOTE_ADDR": "100.90.0.5"}; H = {"X-Token": "secret123"}
res = []
def rec(n, ok, d=""): ok = bool(ok); res.append(ok); print(("PASS " if ok else "FAIL ") + n + ("  " + d if d else ""))
def name_of(r):
    cd = r.headers.get("Content-Disposition", "")
    x = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    return unquote(x.group(1)) if x else re.search(r'filename="?([^";]+)', cd).group(1)
def pc_copy_text(t): grab["v"] = None; _copy(t)
def pc_copy_files(paths): grab["v"] = list(map(str, paths)); state["text"] = ""; state["seq"] += 1
def press(text=None, file=None, empty_file=False):
    data = {}
    if text is not None: data["text"] = text
    if file is not None: data["item"] = (io.BytesIO(file[1]), file[0])
    if empty_file: data["item"] = (io.BytesIO(b""), "")
    r = c.post("/sync", headers=H, environ_base=TS, data=data, content_type="multipart/form-data")
    return r, name_of(r), r.get_data()
def age_pc(sec): m.SYNC.pc_changed_at -= sec

# 1 copy on PC, press within seconds; iPhone has old unsent text -> fetch wins
pc_copy_text("Course link: https://example.com/x")
r, n, body = press(text="old iPhone note")
rec("1 PC copy <30 s -> fetched to iPhone", n == "__razer_text__.txt" and body.decode() == "Course link: https://example.com/x", n)
hist = (m.INBOX / "iPhone text history.txt").read_text(encoding="utf-8")
rec("1b unsent iPhone text kept in history", "old iPhone note" in hist and "not sent" in hist)
# 2 press again; iPhone clipboard now holds the fetched text
r, n, _ = press(text="Course link: https://example.com/x")
rec("2 press again -> nothing new (no bounce)", n == "__nothing__.txt", n)
# 3 copy on iPhone, press -> sent to PC
r, n, body = press(text="Réunion à 14h 😊")
rec("3 new iPhone copy -> sent to PC", n == "__sent__.txt" and state["text"] == "Réunion à 14h 😊", body.decode())
m.SYNC.poll()
rec("3b our own clipboard write not seen as a PC copy", m.SYNC.pc_dirty is False)
r, n, _ = press(text="Réunion à 14h 😊")
rec("4 press again -> nothing new", n == "__nothing__.txt", n)
# 5 PC copy 2 min ago, iPhone unchanged -> fetch
pc_copy_text("copied earlier on PC"); m.SYNC.poll(); age_pc(120)
r, n, body = press(text="Réunion à 14h 😊")
rec("5 older PC copy, iPhone unchanged -> fetched", n == "__razer_text__.txt" and body.decode() == "copied earlier on PC", n)
# 6 PC copy 2 min ago, iPhone just copied -> send
pc_copy_text("stale PC thing"); m.SYNC.poll(); age_pc(120)
r, n, _ = press(text="fresh from WhatsApp")
rec("6 older PC copy, fresh iPhone copy -> sent", n == "__sent__.txt" and state["text"] == "fresh from WhatsApp", n)
# 7 same text with different line endings/accents form -> not new
import unicodedata
r, n, _ = press(text=unicodedata.normalize("NFD", "fresh from WhatsApp\r\n"))
rec("7 same text re-encoded (NFD/CRLF) -> nothing new", n == "__nothing__.txt", n)
# 8 iPhone image -> saved in Downloads; same picture re-encoded -> not new
img = Image.new("RGB", (64, 48), (10, 200, 90)); b1 = io.BytesIO(); img.save(b1, "PNG", compress_level=1)
b2 = io.BytesIO(); img.save(b2, "PNG", compress_level=9)
r, n, _ = press(file=("image.png", b1.getvalue()))
rec("8 iPhone screenshot -> saved to Downloads", n == "__sent__.txt" and (m.INBOX / "image.png").exists(), n)
r, n, _ = press(file=("image.png", b2.getvalue()))
rec("8b same picture, different encoding -> nothing new", n == "__nothing__.txt" and not (m.INBOX / "image (1).png").exists(), n)
# 9 empty iPhone clipboard + recent PC files -> fetch the file
src = work / "src"; src.mkdir(); (src / "CV Sample.pdf").write_bytes(b"%PDF-cv")
pc_copy_files([src / "CV Sample.pdf"])
r, n, body = press(empty_file=True)
rec("9 empty iPhone clipboard + PC file copied -> file fetched", n == "CV Sample.pdf" and body == b"%PDF-cv", n)
r, n, _ = press(empty_file=True)
rec("10 empty + nothing new -> nothing new", n == "__nothing__.txt", n)
# 11 several PC files -> bundle
(src / "b.jpg").write_bytes(b"J"); pc_copy_files([src / "CV Sample.pdf", src / "b.jpg"])
r, n, _ = press(text="fresh from WhatsApp")
rec("11 several PC files -> bundle", n.startswith("Razer bundle"), n)
# 12 restart: state survives, old iPhone clipboard not resent
grab["v"] = None
m.SYNC.save(); s2 = m.SyncState(appdir / "sync-state.json")
rec("12 after restart, same iPhone clipboard is not new", s2.iphone_fp == m.SYNC.iphone_fp and s2.iphone_fp is not None)
# 13 security
r = c.post("/sync", environ_base=TS, data={"text": "x"}); rec("13 no token -> 403", r.status_code == 403)
r = c.post("/sync", headers=H, environ_base={"REMOTE_ADDR": "192.168.1.9"}, data={"text": "x"}); rec("13b LAN -> 403", r.status_code == 403)
# 14 disk full on push -> __error__
from werkzeug.datastructures import FileStorage
orig = FileStorage.save
def boom(self, dst, buffer_size=16384): raise OSError(28, "No space left on device")
FileStorage.save = boom
r, n, body = press(file=("doc.pdf", b"%PDF-new-doc"))
FileStorage.save = orig
rec("14 disk full -> __error__ with message", n == "__error__.txt" and b"could not save" in body, body.decode()[:60])
# 15 old /drop and /pull still work (Share Sheet shortcut)
r = c.post("/drop", headers=H, environ_base=TS, data={"text": "via share sheet"})
rec("15 /drop still works", r.get_data().startswith(b"ok:"))
pc_copy_text("pull me"); r = c.get("/pull", headers=H, environ_base=TS)
rec("15b /pull still works", r.get_data() == b"pull me")
# 16 rapid double press after a PC copy: second press must not resend/refetch weirdly
pc_copy_text("double press test")
r1, n1, _ = press(text="fresh from WhatsApp"); r2, n2, _ = press(text="double press test")
rec("16 double press -> fetch then nothing", n1 == "__razer_text__.txt" and n2 == "__nothing__.txt", f"{n1}, {n2}")
# 17 a background app wipes the PC clipboard just before an iPhone send -> still sent
grab["v"] = None; _copy("")                      # e.g. password manager clears clipboard
r, n, _ = press(text="code 482913 from SMS")
rec("17 PC clipboard wiped by an app -> iPhone copy still sent", n == "__sent__.txt" and state["text"] == "code 482913 from SMS", n)
grab["v"] = None; _copy("")
r, n, _ = press(text="code 482913 from SMS")
rec("17b wiped + nothing new on iPhone -> nothing new", n == "__nothing__.txt", n)
# 18 'item' sent as a Text field (Shortcut misconfigured) -> clear error, both routes
r = c.post("/sync", headers=H, environ_base=TS, data={"item": "master.Rmd"})
rec("18 /sync item-as-text -> explains the File fix", name_of(r) == "__error__.txt" and b"File" in r.get_data(), r.get_data()[:50].decode())
r = c.post("/drop", headers=H, environ_base=TS, data={"item": "master.Rmd"})
rec("18b /drop item-as-text -> explains the File fix", r.status_code == 400 and b"choosing File" in r.get_data())
# 19 PC file -> iPhone, then the iPhone (clipboard now holds that PDF) presses again -> no bounce
pdf = src / "Report (1).pdf"; pdf.write_bytes(b"%PDF-1.7 cv content")
grab["v"] = None; _copy("x"); m.SYNC.poll(); press(text="x")       # settle state
pc_copy_files([pdf])
r, n, body = press(text="unrelated old iPhone text")
rec("19 PC PDF fetched to iPhone", n == "Report (1).pdf", n)
before = set(p.name for p in m.INBOX.iterdir())
r, n, _ = press(file=("Clipboard 29 Oct.pdf", b"%PDF-1.7 cv content"))
rec("19b same PDF pressed back from iPhone -> nothing new, not resent", n == "__nothing__.txt" and set(p.name for p in m.INBOX.iterdir()) == before, n)
# 20 bundle fetched, then the iPhone's multi-file loop sends those files back via /drop -> skipped
f1, f2 = src / "a.docx", src / "b.xlsx"; f1.write_bytes(b"DOCX-a"); f2.write_bytes(b"XLSX-b")
pc_copy_files([f1, f2]); r, n, _ = press(text="unrelated old iPhone text")
rec("20 two PC files -> bundle", n.startswith("Razer bundle"), n)
before = set(p.name for p in m.INBOX.iterdir())
for nm, data in [("a.docx", b"DOCX-a"), ("b.xlsx", b"XLSX-b")]:
    c.post("/drop", headers=H, environ_base=TS, data={"item": (io.BytesIO(data), nm)})
rec("20b those files sent back via Share loop -> skipped, no duplicates", set(p.name for p in m.INBOX.iterdir()) == before)
# 21 PC snip -> iPhone gets PNG; iPhone re-encodes it -> not resent
from PIL import Image as _I
grab["v"] = _I.new("RGB", (30, 20), (1, 2, 3)); state["text"] = ""; state["seq"] += 1
r, n, png = press(text="unrelated old iPhone text")
rec("21 PC snip fetched as PNG", n.endswith(".png"), n)
jb = io.BytesIO(); _I.open(io.BytesIO(png)).save(jb, "PNG", compress_level=0)
r, n, _ = press(file=("image.png", jb.getvalue()))
rec("21b same picture back from iPhone (re-encoded) -> nothing new", n == "__nothing__.txt", n)
# 22 a genuinely new iPhone file still goes through
r, n, _ = press(file=("notes.pdf", b"%PDF brand new"))
rec("22 a new iPhone file still sent", n == "__sent__.txt" and (m.INBOX / "notes.pdf").exists(), n)
# 23 memory survives restart
m.SYNC.save(); s3 = m.SyncState(appdir / "sync-state.json")
rec("23 sent-file memory survives restart", s3.was_sent(m.fingerprint_file(pdf)))
grab["v"] = None
shutil.rmtree(work)
print(f"\n{sum(res)}/{len(res)} passed")
