import sys, os, io, types, pathlib, importlib, tempfile, zipfile, shutil, re
from urllib.parse import unquote
pc = types.ModuleType("pyperclip"); clip = {"v": ""}
pc.copy = lambda t: clip.__setitem__("v", t); pc.paste = lambda: clip["v"]; sys.modules["pyperclip"] = pc
os.environ["DROP_TOKEN"] = "secret123"
sys.path.insert(0, sys.argv[1]); m = importlib.import_module("iphone_drop")
import PIL.ImageGrab as IG
from PIL import Image
work = pathlib.Path(tempfile.mkdtemp(dir=sys.argv[2])); m.OUTBOX = work / "_outbox"
c = m.app.test_client(); TS = {"REMOTE_ADDR": "100.90.0.5"}; H = {"X-Token": "secret123"}
res = []
def rec(n, ok, d=""): ok = bool(ok); res.append(ok); print(("PASS " if ok else "FAIL ") + n + ("  " + d if d else ""))
def name_of(r):
    cd = r.headers.get("Content-Disposition", "")
    m2 = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    return unquote(m2.group(1)) if m2 else re.search(r'filename="?([^";]+)', cd).group(1)
def pull(grab, text):
    IG.grabclipboard = lambda: grab; clip["v"] = text
    return c.get("/pull", headers=H, environ_base=TS)

src = work / "src"; (src / "Course notes" / "week 3").mkdir(parents=True)
(src / "Course notes" / "week 3" / "slides.pdf").write_bytes(b"%PDF-slides")
cv = src / "CV Sample – été.pdf"; cv.write_bytes(b"%PDF-cv")
(src / "photo.jpg").write_bytes(b"JPEG")

r = pull(None, "Réunion à 14h 😊")
rec("text -> __razer_text__.txt, exact content", r.status_code == 200 and name_of(r) == "__razer_text__.txt" and r.get_data().decode() == "Réunion à 14h 😊", name_of(r))
r = pull(None, "")
rec("nothing copied -> __razer_none__", name_of(r) == "__razer_none__.txt")
r = pull([str(cv)], "")
rec("one file -> that file, accented name kept", name_of(r) == "CV Sample – été.pdf" and r.get_data() == b"%PDF-cv", name_of(r))
r = pull([str(src / "Course notes")], "")
z = zipfile.ZipFile(io.BytesIO(r.get_data()))
rec("folder -> FolderName.zip with subfolders", name_of(r) == "Course notes.zip" and "week 3/slides.pdf" in z.namelist(), f"{name_of(r)} {z.namelist()}")
r = pull([str(cv), str(src / "photo.jpg"), str(src / "Course notes")], "")
z = zipfile.ZipFile(io.BytesIO(r.get_data())); nm = sorted(z.namelist())
rec("several items -> 'Razer bundle …zip'", name_of(r).startswith("Razer bundle") and nm == sorted(["CV Sample – été.pdf", "photo.jpg", "Course notes/week 3/slides.pdf"]), f"{name_of(r)} {nm}")
r = pull(Image.new("RGB", (30, 20), "blue"), "")
rec("snip -> PNG", name_of(r).endswith(".png") and r.get_data()[:4] == b"\x89PNG", name_of(r))
r = pull(Image.new("RGB", (30, 20)), "cells from Excel")
rec("Office copy (text+picture) -> text", name_of(r) == "__razer_text__.txt")
r = pull([str(src / "deleted.pdf")], "")
rec("copied file since deleted -> nothing", name_of(r) == "__razer_none__.txt")
old = m.OUTBOX / "old.zip"; old.write_bytes(b"x"); os.utime(old, (0, 0)); pull([str(cv), str(src / "photo.jpg")], "")
rec("outbox cleans files older than 1 h", not old.exists())
r = c.get("/pull", environ_base=TS)
rec("pull without token -> 403", r.status_code == 403)
r = c.get("/pull", headers=H, environ_base={"REMOTE_ADDR": "192.168.1.20"})
rec("pull from LAN -> 403", r.status_code == 403)
shutil.rmtree(work)
print(f"\n{sum(res)}/{len(res)} passed")
