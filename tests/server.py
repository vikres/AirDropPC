# Test wrapper: fake clipboard, allow localhost, optional disk-full injection.
import sys, types, importlib, pathlib, os, ipaddress
store = {"v": "", "fail": int(os.environ.get("CLIP_FAIL", "0"))}
pc = types.ModuleType("pyperclip")
def copy(t):
    if store["fail"] > 0:
        store["fail"] -= 1; raise RuntimeError("clipboard locked")
    store["v"] = t
pc.copy = copy; pc.paste = lambda: store["v"]; sys.modules["pyperclip"] = pc
os.environ["DROP_TOKEN"] = "secret123"
appdir, inbox, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, appdir)
# disk-full injection: any file named DISKFULL* writes half then fails
from werkzeug.datastructures import FileStorage
_orig = FileStorage.save
def save(self, dst, buffer_size=16384):
    if "DISKFULL" in (self.filename or ""):
        with open(dst, "wb") as fh: fh.write(self.stream.read(1000))
        raise OSError(28, "No space left on device")
    return _orig(self, dst, buffer_size)
FileStorage.save = save
m = importlib.import_module("iphone_drop")
m.TAILNET = ipaddress.ip_network("127.0.0.0/8")
m.INBOX = pathlib.Path(inbox); m.INBOX.mkdir(parents=True, exist_ok=True)
m.PORT = port
if hasattr(m, "main"):
    m.main()
else:
    from waitress import serve
    serve(m.app, host="127.0.0.1", port=port, max_request_body_size=m.MAX_BYTES, threads=4)
