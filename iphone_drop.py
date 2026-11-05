"""
iPhone-PC Drop (v8.5): AirDrop-style iPhone <-> Windows PC transfer. Runs on the PC, reached via Tailscale.

  POST /drop   iPhone -> PC
               form field "text" -> Windows clipboard (+ logged); a lone link also opens in the browser
               form file  "item" -> saved to Downloads (HEIC photos converted to JPEG)
               replies "ok: ..." on success, an error message (HTTP 4xx/5xx) otherwise
  GET  /pull   PC -> iPhone: whatever was last copied on the PC -
               text, a file, several files / a folder (zipped), or an image (PNG).
               Text comes back named __razer_text__.txt, nothing copied -> __razer_none__.txt
  POST /sync   ONE button for both directions (Action Button). The iPhone sends its clipboard;
               the receiver decides the direction by recency:
                 1. copied on the PC in the last SYNC_RECENT_SECONDS -> fetch it to the iPhone
                 2. else iPhone clipboard is new                        -> send it to the PC
                 3. else PC clipboard is new                          -> fetch it to the iPhone
                 4. else                                                 -> "Nothing new"
               Reply file names: __sent__ / __nothing__ / __error__ / __razer_text__ / Razer bundle* / a file
  GET  /ping   health check

  Ctrl+Alt+S on the PC (Windows): sends the files/folders selected in Explorer, or
  copied with Ctrl+C, or a copied image (e.g. a Win+Shift+S snip) to the iPhone via
  Taildrop -> iPhone Files app > Tailscale. Folders are zipped first.

Only accepts requests from Tailscale addresses (100.64.0.0/10) carrying the token.

Setup:  double-click Install.cmd (packages, token.txt, firewall rule, autostart)
Debug:  python iphone_drop.py      (shows errors in the window)
Log:    iphone_drop.log next to this file
"""
# The Shortcut matches on the reply names __razer_text__, __razer_none__ and "Razer bundle".
# Renaming them means editing the Shortcut too.
# ---- settings ---------------------------------------------------------------
VERSION = "8.5"
TOKEN = "CHANGE-ME-to-a-long-random-string"   # real token lives in token.txt (made by the installer)
PORT = 8765
MAX_BYTES = 8 * 1024**3        # 8 GB per request
OPEN_LINKS = True              # a single shared link also opens in your browser
CONVERT_HEIC = True            # iPhone HEIC photos -> JPEG (needs pillow-heif)
CLIP_LOG_MAX = 10 * 1024**2    # rotate the text history log at 10 MB
PASTE_FILES = True             # files from the iPhone also go on the PC clipboard (Ctrl+V pastes them)
PASTE_GROUP_SECONDS = 20       # files arriving this close together are pasted together
SYNC_RECENT_SECONDS = 20       # one-button sync: a PC copy this recent wins over the iPhone
HOTKEY = "ctrl+alt+s"          # PC -> iPhone file push; "" disables it
IPHONE = ""                    # iPhone's Tailscale name or 100.x IP; "" = auto-detect
# -----------------------------------------------------------------------------

import hashlib
import hmac
import ipaddress
import logging
import logging.handlers
import os
import pathlib
import json
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import sys
import threading
import time
import unicodedata
import webbrowser

HERE = pathlib.Path(__file__).resolve().parent
LOG_PATH = HERE / "iphone_drop.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=2 * 1024**2,
                                                   backupCount=2, encoding="utf-8")],
)
log = logging.getLogger("iphone_drop")
sys.excepthook = lambda t, v, tb: log.critical("Uncaught error", exc_info=(t, v, tb))

try:
    import pyperclip
    from flask import Flask, Response, abort, request, send_file
except Exception:
    log.exception("Missing package - run: pip install flask waitress pyperclip win11toast pillow pillow-heif")
    raise

try:
    from win11toast import notify
except Exception:
    notify = None

try:
    from PIL import Image
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    Image = None

_TOKEN_FILE = HERE / "token.txt"
if os.environ.get("DROP_TOKEN"):
    TOKEN = os.environ["DROP_TOKEN"]
elif _TOKEN_FILE.exists():
    TOKEN = _TOKEN_FILE.read_text(encoding="utf-8-sig").strip() or TOKEN
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def downloads_dir() -> pathlib.Path:
    """Real Downloads folder (handles OneDrive/relocated Downloads)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            import uuid
            fid = uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}")
            guid = (ctypes.c_byte * 16).from_buffer_copy(fid.bytes_le)
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(p)) == 0:
                path = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                return pathlib.Path(path)
        except Exception:
            pass
    return pathlib.Path.home() / "Downloads"


INBOX = downloads_dir()                                  # files from the iPhone land here

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES
app.config["MAX_FORM_MEMORY_SIZE"] = 50 * 1024**2  # text fields up to 50 MB

_lock = threading.Lock()
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
URL_RE = re.compile(r"^https?://\S+$", re.I)


# ---- helpers ----------------------------------------------------------------
@app.before_request
def guard():
    try:
        ip = ipaddress.ip_address(request.remote_addr)
    except (ValueError, TypeError):
        abort(403)
    if ip not in TAILNET or not hmac.compare_digest(request.headers.get("X-Token", ""), TOKEN):
        log.warning("Refused %s %s from %s", request.method, request.path, request.remote_addr)
        abort(403)


def toast(title, body, path=None):
    if notify:
        try:
            notify(title, body, on_click=str(path) if path else None)
        except Exception:
            log.debug("toast failed", exc_info=True)


def safe_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name)               # iOS sends decomposed accents
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        return f"drop-{time.strftime('%Y%m%d-%H%M%S')}"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.split(".")[0].upper() in WIN_RESERVED:
        stem = "_" + stem
    ext = ext[:20]
    stem = stem[: 150 - len(ext)]                           # stay under Windows' 260-char path limit
    return f"{stem}.{ext}" if ext else stem


def reserve_path(name: str) -> pathlib.Path:
    """Pick a free name and create it atomically (safe with parallel uploads)."""
    base = INBOX / name
    stem, suffix, i = base.stem, base.suffix, 0
    while True:
        dest = base if i == 0 else INBOX / f"{stem} ({i}){suffix}"
        try:
            with open(dest, "xb"):
                return dest
        except FileExistsError:
            i += 1


def set_clipboard(text: str) -> bool:
    for _ in range(10):                                     # another app may hold it briefly
        try:
            with SYNC.lock:
                pyperclip.copy(text)
                SYNC.own_change()                           # our own write is not "a PC copy"
            return True
        except Exception:
            time.sleep(0.2)
    log.error("Clipboard stayed locked; text kept in history log only")
    return False


IS_WINDOWS = os.name == "nt"
_recent_files = []                                      # [(time, path)] for grouping pastes


def dropfiles_bytes(paths) -> bytes:
    """CF_HDROP payload: DROPFILES header (20 bytes, wide chars) + NUL-separated paths + final NUL."""
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)          # pFiles, pt.x, pt.y, fNC, fWide
    body = "".join(str(p) + "\0" for p in paths) + "\0"
    return header + body.encode("utf-16-le")


def _win_set_clipboard_files(paths) -> bool:
    import ctypes
    from ctypes import wintypes
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.argtypes, k32.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], ctypes.c_void_p
    k32.GlobalLock.argtypes, k32.GlobalLock.restype = [ctypes.c_void_p], ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.argtypes, u32.SetClipboardData.restype = [wintypes.UINT, ctypes.c_void_p], ctypes.c_void_p
    u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    u32.CreateWindowExA.argtypes = [wintypes.DWORD, wintypes.LPCSTR, wintypes.LPCSTR, wintypes.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    u32.CreateWindowExA.restype = wintypes.HWND
    u32.DestroyWindow.argtypes = [wintypes.HWND]

    def hglobal(data: bytes):
        h = k32.GlobalAlloc(0x0042, len(data))               # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not h:
            raise OSError("GlobalAlloc failed")
        ptr = k32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        k32.GlobalUnlock(h)
        return h

    # the clipboard must be opened by a window, or SetClipboardData fails (same trick as pyperclip)
    hwnd = u32.CreateWindowExA(0, b"STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    if not hwnd:
        raise OSError("CreateWindowExA failed")
    for _ in range(10):                                      # another app may hold the clipboard
        if u32.OpenClipboard(hwnd):
            break
        time.sleep(0.2)
    else:
        u32.DestroyWindow(hwnd)
        return False
    try:
        u32.EmptyClipboard()
        h = hglobal(dropfiles_bytes(paths))
        if not u32.SetClipboardData(15, h):                  # CF_HDROP
            k32.GlobalFree(h)
            return False
        effect = u32.RegisterClipboardFormatW("Preferred DropEffect")
        h2 = hglobal(struct.pack("<I", 1))                   # DROPEFFECT_COPY: paste copies, never moves
        if not u32.SetClipboardData(effect, h2):
            k32.GlobalFree(h2)
        return True
    finally:
        u32.CloseClipboard()
        u32.DestroyWindow(hwnd)


def set_clipboard_files(paths) -> bool:
    """Put files on the Windows clipboard, grouped with files that arrived moments ago."""
    if not PASTE_FILES or not IS_WINDOWS or not paths:
        return False
    now = time.time()
    with SYNC.lock:
        _recent_files[:] = [(t, p) for t, p in _recent_files
                            if now - t <= PASTE_GROUP_SECONDS and pathlib.Path(p).exists()]
        _recent_files.extend((now, str(p)) for p in paths)
        group = list(dict.fromkeys(p for _, p in _recent_files))
        try:
            ok = _win_set_clipboard_files(group)
        except Exception:
            log.warning("Could not put files on the clipboard", exc_info=True)
            ok = False
        if ok:
            SYNC.own_change()                                # our write is not "a PC copy"
    log.info("Files on clipboard: %d (ok=%s)", len(group), ok)
    return ok


def log_text(text: str):
    path = INBOX / "iPhone text history.txt"
    try:
        if path.exists() and path.stat().st_size > CLIP_LOG_MAX:
            old = path.with_name("iPhone text history (old).txt")
            old.unlink(missing_ok=True)
            path.rename(old)
    except OSError:
        log.warning("Could not rotate text history", exc_info=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{text}\n")


def maybe_convert_heic(dest: pathlib.Path) -> pathlib.Path:
    if not (CONVERT_HEIC and Image and dest.suffix.lower() in (".heic", ".heif")):
        return dest
    try:
        jpg = reserve_path(dest.stem + ".jpg")
        with Image.open(dest) as im:
            exif = im.info.get("exif")
            im.convert("RGB").save(jpg, "JPEG", quality=92, **({"exif": exif} if exif else {}))
        dest.unlink()
        return jpg
    except Exception:
        log.warning("HEIC conversion failed for %s; kept original", dest.name, exc_info=True)
        try:
            jpg.unlink(missing_ok=True)
        except Exception:
            pass
        return dest


# ---- routes -----------------------------------------------------------------
ITEM_AS_TEXT = ("error: the Shortcut sent 'item' as Text, so no file arrived. In that Get Contents of URL, "
                "delete the 'item' field and add it again choosing File, value = the file/Clipboard.")


class SaveError(Exception):
    pass


def ingest(texts, uploads):
    """Text -> clipboard (+ history, links opened); files -> INBOX. Returns (copied, saved paths)."""
    INBOX.mkdir(parents=True, exist_ok=True)
    saved, copied = [], 0

    for text in texts:
        with _lock:
            log_text(text)
            ok = set_clipboard(text)
        copied += ok
        stripped = text.strip()
        if OPEN_LINKS and URL_RE.match(stripped):
            webbrowser.open(stripped)
        log.info("Text received (%d chars, clipboard=%s)", len(text), ok)

    for f in uploads:
        dest = reserve_path(safe_name(f.filename or ""))
        try:
            f.save(dest)                                    # streams to disk
        except Exception:
            dest.unlink(missing_ok=True)                    # never leave half-written files
            log.exception("Saving %s failed", f.filename)
            raise SaveError(f"error: could not save {f.filename} (disk full?) - "
                            f"{len(saved)} file(s) saved before the failure")
        if SYNC.was_sent(fingerprint_file(dest)):          # it just came from the PC: don't duplicate
            dest.unlink(missing_ok=True)
            log.info("Skipped %s: it came from the PC", f.filename)
            continue
        dest = maybe_convert_heic(dest)
        saved.append(dest)
        log.info("File saved: %s (%d bytes)", dest.name, dest.stat().st_size)

    pasted = set_clipboard_files(saved) if saved else False
    hint = " - Ctrl+V to paste" if pasted else ""

    # one notification per send, not one per photo
    if saved and texts:
        toast("From iPhone", f"{len(saved)} file(s) + text copied", INBOX)
    elif len(saved) == 1:
        toast("File from iPhone" + hint, saved[0].name, saved[0])
    elif saved:
        toast("Files from iPhone" + hint, f"{len(saved)} files in Downloads", INBOX)
    elif texts:
        toast("Copied from iPhone" if copied else "Clipboard busy - text saved to history",
              texts[-1].strip()[:150])

    return copied, saved


def describe_request() -> str:
    """What the Shortcut actually sent (field names, sizes, file names) - for the log."""
    fields = {k: len(v) for k, v in request.form.items(multi=True)}
    files = [(k, f.filename or "(no name)", f.mimetype) for k, f in request.files.items(multi=True)]
    return f"fields={fields} files={files} bytes={request.content_length}"


def _real_uploads():
    return [f for f in request.files.getlist("item") if f is not None]


@app.post("/drop")
def drop():
    texts = [t for t in request.form.getlist("text") if t.strip()]
    uploads = request.files.getlist("item")
    if not texts and not uploads:
        log.warning("Empty drop: %s", describe_request())
        if "item" in request.form:
            return Response(ITEM_AS_TEXT, 400)
        return Response("error: nothing received - check the Shortcut's form field names", 400)
    try:
        copied, saved = ingest(texts, uploads)
    except SaveError as e:
        return Response(str(e), 507)
    parts = ([f"{copied} text"] if texts else []) + ([f"{len(saved)} file(s)"] if saved else [])
    return f"ok: {', '.join(parts)}"


OUTBOX = HERE / "_outbox"                               # temporary zips/PNGs for /pull


def read_pc_clipboard():
    """('files', [paths]) | ('text', str) | ('image', PIL.Image) | ('none', None).
    Files win, then text (Office copies also carry a picture), then a bare image (a snip)."""
    from PIL import ImageGrab
    data = None
    for _ in range(5):
        try:
            data = ImageGrab.grabclipboard()
            break
        except Exception:
            time.sleep(0.1)
    if isinstance(data, list):
        paths = [p for p in data if os.path.exists(p)]
        if paths:
            return "files", paths
    try:
        text = pyperclip.paste()
    except Exception:
        text = ""
    if text and text.strip():
        return "text", text
    if data is not None and hasattr(data, "save"):
        return "image", data
    return "none", None


def _clean_outbox():
    OUTBOX.mkdir(exist_ok=True)
    for f in OUTBOX.iterdir():
        try:
            if time.time() - f.stat().st_mtime > 3600:
                f.unlink()
        except OSError:
            pass


def _named(text: str, name: str):
    return Response(text.encode("utf-8"), mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/pull")
def pull():
    return pull_response()[0]


def pull_response():
    """(Response, kind, text-or-None) for whatever was last copied on the PC."""
    kind, data = read_pc_clipboard()
    resp = _pull_build(kind, data)
    return resp, kind, (data if kind == "text" else None)


_last_served = []                                         # files the last pull handed to the iPhone


def _pull_build(kind, data):
    _last_served.clear()
    if kind == "text":
        log.info("Pull: text (%d chars)", len(data))
        return _named(data, "__razer_text__.txt")
    if kind == "none":
        return _named("Nothing copied on the PC", "__razer_none__.txt")
    _clean_outbox()
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    if kind == "image":
        out = OUTBOX / f"PC {stamp}.png"
        data.save(out, "PNG")
        _last_served.append(out)
        log.info("Pull: image")
        return send_file(out, as_attachment=True, download_name=out.name)
    paths = [pathlib.Path(p) for p in data]
    if len(paths) == 1 and paths[0].is_file():
        log.info("Pull: %s", paths[0].name)
        _last_served.append(paths[0])
        return send_file(paths[0], as_attachment=True, download_name=paths[0].name)
    if len(paths) == 1:                                   # one folder -> FolderName.zip
        base = OUTBOX / f"{safe_name(paths[0].name)} {stamp}"
        out = pathlib.Path(shutil.make_archive(str(base), "zip", paths[0]))
        name = f"{paths[0].name}.zip"
        _last_served.append(out)
    else:                                                 # several items -> bundle, auto-extracted by the Shortcut
        out = OUTBOX / f"Razer bundle {stamp}.zip"
        import zipfile
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in paths:
                if p.is_dir():
                    for f in p.rglob("*"):
                        if f.is_file():
                            z.write(f, pathlib.Path(p.name) / f.relative_to(p))
                else:
                    z.write(p, p.name)
        name = out.name
        for p in paths:                                   # the Shortcut unpacks a bundle into its files
            _last_served.extend([f for f in p.rglob("*") if f.is_file()] if p.is_dir() else [p])
    log.info("Pull: %d item(s) as %s", len(paths), name)
    return send_file(out, as_attachment=True, download_name=name)


# ---- one-button sync (Action Button) ---------------------------------------------------
def clip_seq() -> int:
    """Windows clipboard change counter (tests replace this)."""
    if os.name == "nt":
        import ctypes
        return ctypes.windll.user32.GetClipboardSequenceNumber()
    return 0


class SyncState:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.path = path
        self.seen_seq = None            # last clipboard counter we accounted for
        self.pc_changed_at = 0.0     # when the PC clipboard last changed by a real copy
        self.pc_dirty = False        # PC has a copy the iPhone hasn't fetched
        self.iphone_fp = None           # fingerprint of the last iPhone clipboard we saw
        self.sent_fp = None             # fingerprint of the last text we gave the iPhone
        self.sent_fps = []              # fingerprints of everything recently given to the iPhone
        self.load()

    def load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.iphone_fp, self.sent_fp = d.get("iphone_fp"), d.get("sent_fp")
            self.sent_fps = list(d.get("sent_fps") or [])
        except Exception:
            pass

    def save(self):
        try:
            self.path.write_text(json.dumps({"iphone_fp": self.iphone_fp, "sent_fp": self.sent_fp,
                                             "sent_fps": self.sent_fps[-100:]}),
                                 encoding="utf-8")
        except OSError:
            log.warning("Could not save sync state", exc_info=True)

    def remember_sent(self, fps):
        with self.lock:
            for fp in fps:
                if fp and fp not in self.sent_fps:
                    self.sent_fps.append(fp)
            self.sent_fps = self.sent_fps[-100:]

    def was_sent(self, fp) -> bool:
        return fp is not None and (fp == self.sent_fp or fp in self.sent_fps)

    def poll(self):
        """Notice real copies on the PC. Called by the watcher thread and before each sync."""
        with self.lock:
            s = clip_seq()
            if self.seen_seq is None:
                self.seen_seq = s                           # whatever was there at start = old
            elif s != self.seen_seq:
                self.seen_seq = s
                self.pc_changed_at = time.time()
                self.pc_dirty = True

    def own_change(self):
        with self.lock:
            self.seen_seq = clip_seq()
            self.pc_dirty = False


SYNC = SyncState(HERE / "sync-state.json")


def clipboard_watcher():
    while True:
        try:
            SYNC.poll()
        except Exception:
            log.debug("watcher", exc_info=True)
        time.sleep(0.3)


def _norm_text(t: str) -> str:
    return unicodedata.normalize("NFC", t).replace("\r\n", "\n").strip()


def fingerprint_text(t: str) -> str:
    return "t:" + hashlib.sha256(_norm_text(t).encode("utf-8")).hexdigest()


def fingerprint_bytes(data: bytes) -> str:
    """Images by their pixels (iOS may re-encode the same picture), other files by bytes."""
    if Image is not None:
        try:
            import io
            with Image.open(io.BytesIO(data)) as im:
                im = im.convert("RGB")
                return "i:" + hashlib.sha256(repr(im.size).encode() + im.tobytes()).hexdigest()
        except Exception:
            pass
    return "b:" + hashlib.sha256(data).hexdigest()


def fingerprint_file(path) -> str:
    """Same result as fingerprint_bytes(file contents), without loading big files into memory."""
    path = pathlib.Path(path)
    if Image is not None:
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                return "i:" + hashlib.sha256(repr(im.size).encode() + im.tobytes()).hexdigest()
        except Exception:
            pass
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return "b:" + h.hexdigest()


@app.post("/sync")
def sync():
    log.info("Sync request: %s", describe_request())
    texts = [t for t in request.form.getlist("text") if t.strip()]
    uploads = _real_uploads()
    if not texts and not any(f.filename for f in uploads) and request.form.get("item", "").strip():
        log.warning("Sync: 'item' arrived as text (fields=%s)", list(request.form))
        return _named(ITEM_AS_TEXT, "__error__.txt")
    try:
        with SYNC.lock:
            SYNC.poll()
            # what the iPhone has, and is it new?
            fp, blobs = None, []
            if texts:
                fp = fingerprint_text(texts[0])
            elif uploads:
                blobs = [(f.filename or "", f.mimetype, f.read()) for f in uploads]
                blobs = [b for b in blobs if b[2]]
                if blobs:
                    fp = fingerprint_bytes(blobs[0][2])
            iphone_new = fp is not None and fp != SYNC.iphone_fp and not SYNC.was_sent(fp)
            pc_recent = SYNC.pc_dirty and time.time() - SYNC.pc_changed_at <= SYNC_RECENT_SECONDS

            if pc_recent or (not iphone_new and SYNC.pc_dirty):
                direction = "pull"
            elif iphone_new:
                direction = "push"
            else:
                direction = "nothing"

            pulled = None
            if direction == "pull":
                pulled = read_pc_clipboard()
                if pulled[0] == "none":                     # clipboard wiped/touched by an app,
                    SYNC.pc_dirty = False                # not a real copy: never let it win
                    direction = "push" if iphone_new else "nothing"

            if fp is not None:
                if iphone_new and direction == "pull" and texts:
                    log_text(texts[0] + "\n(iPhone text not sent: the PC copy was newer)")
                SYNC.iphone_fp = fp

        if direction == "push":
            from werkzeug.datastructures import FileStorage
            import io
            files = [FileStorage(io.BytesIO(d), filename=n or "iPhone clipboard.png", content_type=m)
                     for n, m, d in blobs]
            copied, saved = ingest(texts[:1], files)
            SYNC.save()
            what = "text" if texts else (saved[0].name if saved else "item")
            log.info("Sync: iPhone -> PC (%s)", what)
            return _named(f"Sent to PC: {what}", "__sent__.txt")

        if direction == "pull":
            kind, data = pulled
            resp, text = _pull_build(kind, data), (data if kind == "text" else None)
            with SYNC.lock:
                SYNC.pc_dirty = False
                if kind == "text":
                    SYNC.sent_fp = fingerprint_text(text)
                    SYNC.remember_sent([SYNC.sent_fp])
                else:                                       # so a copied-back file isn't resent
                    SYNC.remember_sent([fingerprint_file(f) for f in _last_served])
            SYNC.save()
            log.info("Sync: PC -> iPhone (%s)", kind)
            if kind == "none":
                return _named("Nothing new", "__nothing__.txt")
            return resp

        SYNC.save()
        log.info("Sync: nothing new (iPhone sent %s)", "text" if texts else ("file" if fp else "nothing"))
        return _named("Nothing new on either side", "__nothing__.txt")
    except SaveError as e:
        return _named(str(e), "__error__.txt")
    except Exception as e:
        log.exception("Sync failed")
        return _named(f"error: {type(e).__name__} - see iphone_drop.log on the PC", "__error__.txt")


@app.get("/ping")
def ping():
    return "pong"


@app.errorhandler(Exception)
def on_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    log.exception("Unhandled error")
    return Response(f"error: {type(e).__name__} - see iphone_drop.log on the PC", 500)



# ---- PC -> iPhone: Ctrl+Alt+S pushes files via Taildrop ----------------------
NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # don't flash a console under pythonw
TERMINALS = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty",
             "PuTTY", "VirtualConsoleClass"}         # never send Ctrl+C there (= interrupt)
_send_busy = threading.Lock()


def _tmpdir():
    kw = {"ignore_cleanup_errors": True} if sys.version_info >= (3, 10) else {}
    return tempfile.TemporaryDirectory(**kw)


def tailscale_exe():
    exe = shutil.which("tailscale")
    if exe:
        return exe
    for p in (r"C:\Program Files\Tailscale\tailscale.exe",
              r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
        if os.path.exists(p):
            return p
    return None


def run_tailscale(*args, timeout=3600):
    exe = tailscale_exe()
    if not exe:
        raise RuntimeError("tailscale.exe not found - is Tailscale installed?")
    return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=NO_WINDOW)


def pick_iphone(status: dict):
    """(name, ip) of the iPhone in `tailscale status --json`; prefers online + 'iphone' in name."""
    if IPHONE:
        return IPHONE, IPHONE
    best = None
    for peer in (status.get("Peer") or {}).values():
        if str(peer.get("OS", "")).lower() != "ios":
            continue
        ips = [ip for ip in (peer.get("TailscaleIPs") or []) if "." in ip]
        if not ips:
            continue
        name = peer.get("HostName") or peer.get("DNSName", "").split(".")[0] or ips[0]
        rank = (bool(peer.get("Online")), "iphone" in name.lower())
        if best is None or rank > best[0]:
            best = (rank, name, ips[0])
    return (best[1], best[2]) if best else None


def prepare_paths(paths, workdir):
    """Existing files as-is; folders zipped into workdir."""
    out = []
    for p in paths:
        p = pathlib.Path(p)
        if p.is_dir():
            out.append(pathlib.Path(shutil.make_archive(str(pathlib.Path(workdir) / p.name), "zip", p)))
        elif p.is_file():
            out.append(p)
    return out


def send_to_iphone(paths):
    with _tmpdir() as work:
        files = prepare_paths(paths, work)
        if not files:
            toast("Nothing sent", "No files found to send")
            return False
        st = run_tailscale("status", "--json", timeout=15)
        target = pick_iphone(json.loads(st.stdout or "{}"))
        if not target:
            toast("Nothing sent", "No iPhone found in Tailscale - is it signed in?")
            log.error("No iOS peer in tailscale status")
            return False
        name, ip = target
        label = files[0].name if len(files) == 1 else f"{len(files)} files"
        toast("Sending to iPhone...", label)
        r = run_tailscale("file", "cp", *map(str, files), f"{ip}:")
        if r.returncode != 0:
            err = (r.stderr or r.stdout).strip()
            log.error("Taildrop to %s failed: %s", name, err)
            toast("Send to iPhone failed", err[:150] or "see iphone_drop.log")
            return False
        log.info("Sent %d file(s) to %s via Taildrop", len(files), name)
        toast("Sent to iPhone", f"{label} - Files app > Tailscale")
        return True


def _win_copy_selection():
    """Press Ctrl+C in the active window (unless it's a terminal); wait for the clipboard."""
    import ctypes
    u = ctypes.windll.user32
    deadline = time.time() + 2                          # let go of Ctrl/Alt/S first
    while time.time() < deadline and any(u.GetAsyncKeyState(k) & 0x8000 for k in (0x11, 0x12, 0x10, 0x5B)):
        time.sleep(0.02)
    buf = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(u.GetForegroundWindow(), buf, 256)
    if buf.value in TERMINALS:
        return
    seq = u.GetClipboardSequenceNumber()
    for vk, up in ((0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2)):   # Ctrl down, C down, C up, Ctrl up
        u.keybd_event(vk, 0, up, 0)
    for _ in range(30):
        time.sleep(0.02)
        if u.GetClipboardSequenceNumber() != seq:
            break
    time.sleep(0.15)                                    # let the app finish writing all formats


def clipboard_files(work):
    """Files on the clipboard, or a copied image saved as PNG; text is ignored (iPhone pulls text)."""
    from PIL import ImageGrab
    data = None
    for _ in range(5):
        try:
            data = ImageGrab.grabclipboard()
            break
        except Exception:
            time.sleep(0.1)
    if isinstance(data, list):
        return [p for p in data if os.path.exists(p)], False
    try:
        text = pyperclip.paste()
    except Exception:
        text = ""
    if text and text.strip():
        return [], True                                 # text (Office also adds a picture: ignore it)
    if data is not None and hasattr(data, "save"):
        png = pathlib.Path(work) / f"Clipboard {time.strftime('%Y-%m-%d %H-%M-%S')}.png"
        data.save(png, "PNG")
        return [png], False
    return [], False


def on_hotkey():
    if not _send_busy.acquire(blocking=False):          # ignore repeat presses while sending
        return
    try:
        _win_copy_selection()
        with _tmpdir() as work:
            files, is_text = clipboard_files(work)
            if files:
                send_to_iphone(files)
            elif is_text:
                toast("Text stays here", "Triple-tap the back of your iPhone to fetch it")
            else:
                toast("Nothing to send", "Select or copy files first")
    except RuntimeError as e:                           # e.g. Tailscale not installed
        log.error("Hotkey action failed: %s", e)
        toast("Send to iPhone failed", str(e)[:150])
    except Exception:
        log.exception("Hotkey action failed")
        toast("Send to iPhone failed", "see iphone_drop.log")
    finally:
        _send_busy.release()


def parse_hotkey(spec: str):
    mods_map = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
    mods, key = 0, None
    for part in [p.strip().lower() for p in spec.split("+") if p.strip()]:
        if part in mods_map:
            mods |= mods_map[part]
        elif len(part) == 1 and part.isascii() and part.isalnum():
            key = ord(part.upper())
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            key = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f"Unknown key in HOTKEY: {part!r}")
    if key is None or not mods:
        raise ValueError("HOTKEY needs at least one modifier and one key, e.g. 'ctrl+alt+s'")
    return mods, key


def hotkey_loop():
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    try:
        mods, key = parse_hotkey(HOTKEY)
    except ValueError as e:
        log.error("%s", e)
        return
    if not u.RegisterHotKey(None, 1, mods | 0x4000, key):   # 0x4000 = MOD_NOREPEAT
        log.error("%s is already used by another app - change HOTKEY", HOTKEY)
        toast("Hotkey unavailable", f"{HOTKEY} is taken by another app")
        return
    log.info("Hotkey %s active", HOTKEY)
    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:                        # WM_HOTKEY
            threading.Thread(target=on_hotkey, daemon=True).start()


# ---- startup ----------------------------------------------------------------
def already_running() -> bool:
    if os.name == "nt":                                     # Windows can let two processes share a port
        import ctypes
        global _mutex
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\iphone_drop_receiver")
        if ctypes.windll.kernel32.GetLastError() == 183:    # ERROR_ALREADY_EXISTS
            return True
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
        return True                                         # something already listens on the port
    except OSError:
        return False


def main():
    if TOKEN.startswith("CHANGE-ME"):
        log.error("Token not set")
        sys.exit("No token yet - run Install.cmd (it creates token.txt).")
    if already_running():
        log.info("Another instance is already running - exiting")
        sys.exit(0)
    INBOX.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=clipboard_watcher, daemon=True, name="clipwatch").start()
    if os.name == "nt" and HOTKEY:
        threading.Thread(target=hotkey_loop, daemon=True, name="hotkey").start()
    from waitress import serve
    log.info("Receiver v%s started on port %d, saving to %s (HEIC->JPEG %s)",
             VERSION, PORT, INBOX, "on" if (CONVERT_HEIC and Image) else "off")
    # waitress caps bodies at 1 GB by default; raise it.
    serve(app, host="0.0.0.0", port=PORT, max_request_body_size=MAX_BYTES, threads=4)


if __name__ == "__main__":
    main()
