import sys, os, json, types, pathlib, importlib, tempfile, zipfile, shutil
S = sys.argv[2]
pc = types.ModuleType("pyperclip"); clip = {"v": ""}
pc.copy = lambda t: clip.__setitem__("v", t); pc.paste = lambda: clip["v"]; sys.modules["pyperclip"] = pc
sys.path.insert(0, sys.argv[1]); m = importlib.import_module("iphone_drop")
toasts = []; m.toast = lambda t, b, p=None: toasts.append((t, b))
res = []
def rec(n, ok, d=""): ok = bool(ok); res.append(ok); print(("PASS " if ok else "FAIL ") + n + ("  " + d if d else ""))

# hotkey parsing
rec("parse ctrl+alt+s", m.parse_hotkey("ctrl+alt+s") == (0x3, ord("S")))
rec("parse ctrl+shift+F9", m.parse_hotkey("Ctrl + Shift + F9") == (0x6, 0x78))
for bad in ["s", "ctrl+alt", "ctrl+alt+é"]:
    try: m.parse_hotkey(bad); rec(f"reject {bad!r}", False)
    except ValueError: rec(f"reject {bad!r}", True)

# iPhone detection
status = {"Self": {"OS": "windows"}, "Peer": {
  "a": {"HostName": "My-iPad", "OS": "iOS", "Online": True, "TailscaleIPs": ["100.80.1.1", "fd7a::1"]},
  "b": {"HostName": "My-iPhone", "OS": "iOS", "Online": True, "TailscaleIPs": ["fd7a::2", "100.80.1.2"]},
  "c": {"HostName": "old-iphone", "OS": "iOS", "Online": False, "TailscaleIPs": ["100.80.1.3"]},
  "d": {"HostName": "my-pc", "OS": "windows", "Online": True, "TailscaleIPs": ["100.80.1.9"]}}}
rec("picks online iPhone over iPad/offline", m.pick_iphone(status) == ("My-iPhone", "100.80.1.2"), str(m.pick_iphone(status)))
rec("no iOS peer -> None", m.pick_iphone({"Peer": {"d": status["Peer"]["d"]}}) is None)
rec("Peer null handled", m.pick_iphone({"Peer": None}) is None)

# full send through fake tailscale
work = pathlib.Path(tempfile.mkdtemp(dir=S)); recv = work / "recv"; recv.mkdir()
(work / "status.json").write_text(json.dumps(status))
os.environ.update(FAKE_TS_LOG=str(work / "ts.log"), FAKE_TS_STATUS=str(work / "status.json"), FAKE_TS_RECV=str(recv))
os.chmod(os.path.join(S, "fakets", "tailscale"), 0o755)  # copies can lose the exec bit
os.environ["PATH"] = os.path.join(S, "fakets") + os.pathsep + os.environ["PATH"]
src = work / "src"; (src / "Course notes" / "sub").mkdir(parents=True)
(src / "Course notes" / "sub" / "slides.pdf").write_bytes(b"%PDF"); (src / "CV Sample.pdf").write_bytes(b"%PDF")
ok = m.send_to_iphone([src / "CV Sample.pdf", src / "Course notes", src / "missing.txt"])
got = sorted(p.name for p in recv.iterdir())
cmd = (work / "ts.log").read_text().splitlines()[-1]
rec("file + folder sent, folder zipped", ok and got == ["CV Sample.pdf", "Course notes.zip"], str(got))
rec("zip keeps folder contents", "sub/slides.pdf" in zipfile.ZipFile(recv / "Course notes.zip").namelist())
rec("sent to iPhone IP", cmd.endswith("100.80.1.2:"), cmd[-30:])
rec("success toast", toasts[-1][0] == "Sent to iPhone", str(toasts[-1]))
os.environ["FAKE_TS_FAIL"] = "1"; ok = m.send_to_iphone([src / "CV Sample.pdf"]); del os.environ["FAKE_TS_FAIL"]
rec("Taildrop failure reported", not ok and toasts[-1][0] == "Send to iPhone failed" and "no such peer" in toasts[-1][1], str(toasts[-1]))
(work / "status.json").write_text(json.dumps({"Peer": {}}))
rec("no iPhone -> clear message", not m.send_to_iphone([src / "CV Sample.pdf"]) and "No iPhone" in toasts[-1][1])
(work / "status.json").write_text(json.dumps(status))
os.environ["PATH"] = "/nonexistent"; _w = m.shutil.which; m.shutil.which = lambda *a: None
m._win_copy_selection = lambda: None
import PIL.ImageGrab as _IG; _IG.grabclipboard = lambda: [str(src / "CV Sample.pdf")]
_orig_exists = m.os.path.exists; m.os.path.exists = lambda p: False if "Tailscale" in str(p) else _orig_exists(p)
m.on_hotkey(); m.shutil.which = _w; m.os.path.exists = _orig_exists
os.environ["PATH"] = os.path.join(S, "fakets") + os.pathsep + "/usr/bin:/bin"
rec("Tailscale missing -> says so", "tailscale.exe not found" in toasts[-1][1], str(toasts[-1]))
rec("nothing valid -> no call", not m.send_to_iphone([src / "nope.pdf"]) and toasts[-1][0] == "Nothing sent")

# hotkey action logic with clipboard mocked (Windows parts stubbed)
m._win_copy_selection = lambda: None
from PIL import Image
import PIL.ImageGrab as IG
for name, grab, text, expect in [
    ("copied files -> sent", [str(src / "CV Sample.pdf")], "", "Sent to iPhone"),
    ("copied text -> told to triple-tap", None, "hello", "Text stays here"),
    ("Office copy (text+picture) -> treated as text", Image.new("RGB", (5, 5)), "cell A1", "Text stays here"),
    ("snip (image only) -> sent as PNG", Image.new("RGB", (40, 30), "red"), "", "Sent to iPhone"),
    ("empty clipboard -> hint", None, "", "Nothing to send")]:
    IG.grabclipboard = (lambda g=grab: g); clip["v"] = text
    for f in recv.iterdir(): f.unlink()
    m.on_hotkey()
    extra = sorted(p.name for p in recv.iterdir())
    rec(name, toasts[-1][0] == expect and (expect != "Sent to iPhone" or extra), f"{toasts[-1][0]} {extra}")
# double press while busy is ignored
m._send_busy.acquire(); n = len(toasts); m.on_hotkey(); m._send_busy.release()
rec("repeat press while sending ignored", len(toasts) == n)
shutil.rmtree(work)
print(f"\n{sum(res)}/{len(res)} passed")
