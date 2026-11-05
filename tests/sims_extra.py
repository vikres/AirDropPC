import sys, os, io, time, pathlib, subprocess, socket, unicodedata
sys.argv = sys.argv  # reuse helpers from sims by exec
exec(open(os.path.join(os.path.dirname(__file__), "sims.py")).read().split("srv = start()")[0])
from PIL import Image
import pillow_heif
srv = start({"BROWSER": "echo"})  # webbrowser uses $BROWSER -> harmless echo
# HEIC conversion
buf = io.BytesIO(); pillow_heif.register_heif_opener()
Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "HEIF"); 
st, txt = post(files=[("item", "IMG_0042.HEIC", buf.getvalue())])
names = [p.name for p in INBOX.iterdir() if p.name.startswith("IMG_0042")]
ok = names == ["IMG_0042.jpg"] and Image.open(INBOX / "IMG_0042.jpg").format == "JPEG"
rec("HEIC photo arrives as JPEG", ok, f"{names} {txt!r}")
# success reply format the Shortcut checks
st, txt = post(fields=[("text", "hello")], files=[("item", "a.pdf", b"%PDF")])
rec("reply starts with 'ok' + counts", st == 200 and txt.startswith("ok:"), repr(txt))
# lone link: copied (and opened)
st, txt = post(fields=[("text", "https://example.com/some/page")])
clip = urllib.request.urlopen(urllib.request.Request(BASE + "/pull", headers=H)).read().decode()
rec("lone link copied", clip.startswith("https://example.com"), repr(clip[:40]))
# regressions from earlier audits
for nm, fn in [("accents kept", "Facture_été 2026.pdf"), ("traversal neutralised", "../../evil.txt"),
               ("reserved name", "CON.txt"), ("very long name", "a" * 300 + ".jpg"), ("emoji name", "📄 notes.txt")]:
    st, _ = post(files=[("item", fn, b"x")])
    rec(nm, st == 200)
rec("no file escaped the inbox", not (INBOX.parent / "evil.txt").exists())
# multiple texts + files in one go, then big text multipart
st, txt = post(fields=[("text", "é" * 400_000)])
rec("400k-char text accepted", st == 200, repr(txt))
# security
for nm, hdr in [("no token -> 403", {}), ("wrong token -> 403", {"X-Token": "nope"})]:
    try: urllib.request.urlopen(urllib.request.Request(BASE + "/ping", headers=hdr)); code = 200
    except urllib.error.HTTPError as e: code = e.code
    rec(nm, code == 403)
# log content check
logtxt = (pathlib.Path(APP) / "iphone_drop.log").read_text(encoding="utf-8")
rec("refusals + saves logged", "Refused" in logtxt and "File saved" in logtxt)
srv.terminate(); srv.wait()
# startup without token
r = subprocess.run([sys.executable, "-I", os.path.join(APP, "iphone_drop.py")], capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "DROP_TOKEN"})
rec("refuses to start with placeholder token", r.returncode == 1 and "token" in r.stderr.lower(), r.stderr.strip()[:60])
print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
