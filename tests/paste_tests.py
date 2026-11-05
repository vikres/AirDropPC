import sys, os, io, types, pathlib, importlib, tempfile, shutil, struct
APP, TMP = sys.argv[1], sys.argv[2]
state = {"text": "", "seq": 1}
pc = types.ModuleType("pyperclip")
def _copy(t): state["text"] = t; state["seq"] += 1
pc.copy = _copy; pc.paste = lambda: state["text"]; sys.modules["pyperclip"] = pc
os.environ["DROP_TOKEN"] = "secret123"
work = pathlib.Path(tempfile.mkdtemp(dir=TMP)); appdir = work / "app"
appdir.mkdir(parents=True); shutil.copy2(os.path.join(APP, "iphone_drop.py"), appdir)  # receiver only
sys.path.insert(0, str(appdir)); m = importlib.import_module("iphone_drop")
m.clip_seq = lambda: state["seq"]; m.INBOX = work / "Downloads"; m.INBOX.mkdir()
m.IS_WINDOWS = True; m.OPEN_LINKS = False
calls, toasts = [], []
def fake_win(paths): calls.append([pathlib.Path(p).name for p in paths]); state["seq"] += 1; return True
m._win_set_clipboard_files = fake_win; m.toast = lambda t, b, p=None: toasts.append(t)
m.SYNC.poll()
c = m.app.test_client(); TS = {"REMOTE_ADDR": "100.90.0.5"}; H = {"X-Token": "secret123"}
res = []
def rec(n, ok, d=""): ok = bool(ok); res.append(ok); print(("PASS " if ok else "FAIL ") + n + ("  " + d if d else ""))

# 1 CF_HDROP layout
b = m.dropfiles_bytes([r"C:\Users\alex\Downloads\Résumé été.pdf", r"C:\x\b.jpg"])
pFiles, x, y, fnc, wide = struct.unpack("<IiiII", b[:20]); body = b[20:].decode("utf-16-le")
rec("1 DROPFILES header correct", (pFiles, fnc, wide) == (20, 0, 1))
rec("1b paths NUL-separated, double-NUL end, accents kept", body == "C:\\Users\\alex\\Downloads\\Résumé été.pdf\0C:\\x\\b.jpg\0\0")
# 2 single file via Action Button -> on clipboard
r = c.post("/sync", headers=H, environ_base=TS, data={"item": (io.BytesIO(b"PPTX" * 100), "Clipboard 22 Oct.pptx")})
rec("2 file sent -> on PC clipboard", calls[-1] == ["Clipboard 22 Oct.pptx"], str(calls[-1:]))
rec("2b toast says Ctrl+V", "Ctrl+V" in toasts[-1], toasts[-1])
m.SYNC.poll(); rec("2c clipboard write not mistaken for a PC copy", m.SYNC.pc_dirty is False)
# 3 several files via Share (one request each, seconds apart) -> pasted together
for i in range(3):
    c.post("/drop", headers=H, environ_base=TS, data={"item": (io.BytesIO(b"x%d" % i), f"doc{i}.pdf")})
rec("3 files sent one by one -> grouped on clipboard", calls[-1] == ["Clipboard 22 Oct.pptx", "doc0.pdf", "doc1.pdf", "doc2.pdf"], str(calls[-1]))
# 4 older than the group window -> new paste starts fresh
m._recent_files[:] = [(t - 60, p) for t, p in m._recent_files]
c.post("/drop", headers=H, environ_base=TS, data={"item": (io.BytesIO(b"new"), "later.pdf")})
rec("4 file a minute later -> pasted alone", calls[-1] == ["later.pdf"], str(calls[-1]))
# 5 file deleted in between -> not offered for paste
(m.INBOX / "later.pdf").unlink()
c.post("/drop", headers=H, environ_base=TS, data={"item": (io.BytesIO(b"n2"), "next.pdf")})
rec("5 deleted file dropped from the group", calls[-1] == ["next.pdf"], str(calls[-1]))
# 6 HEIC converted -> the JPG is what gets pasted
from PIL import Image; import pillow_heif; pillow_heif.register_heif_opener()
buf = io.BytesIO(); Image.new("RGB", (20, 20), "red").save(buf, "HEIF")
c.post("/drop", headers=H, environ_base=TS, data={"item": (io.BytesIO(buf.getvalue()), "IMG_1.HEIC")})
rec("6 HEIC photo -> JPG on clipboard", calls[-1][-1] == "IMG_1.jpg", str(calls[-1]))
# 7 clipboard API failure -> file still saved, reply still ok, no crash
def boom(paths): raise OSError("clipboard locked")
m._win_set_clipboard_files = boom
r = c.post("/drop", headers=H, environ_base=TS, data={"item": (io.BytesIO(b"z"), "safe.pdf")})
rec("7 clipboard failure -> file saved, ok reply", r.get_data().startswith(b"ok:") and (m.INBOX / "safe.pdf").exists() and "Ctrl+V" not in toasts[-1])
# 8 text still goes to clipboard as text
m._win_set_clipboard_files = fake_win; n = len(calls)
c.post("/drop", headers=H, environ_base=TS, data={"text": "hello"})
rec("8 text unaffected (no file paste)", state["text"] == "hello" and len(calls) == n)
# 9 setting off
m.PASTE_FILES = False; n = len(calls)
c.post("/drop", headers=H, environ_base=TS, data={"item": (io.BytesIO(b"q"), "q.pdf")})
rec("9 PASTE_FILES=False -> nothing put on clipboard", len(calls) == n)
shutil.rmtree(work)
print(f"\n{sum(res)}/{len(res)} passed")
