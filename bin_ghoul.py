#!/usr/bin/env python3
"""
Bin-Ghoul: Echoes from the Recycle Bin

Reads your Recycle Bin (read-only), then asks a LOCAL open-weight model running
in Ollama to write a ghostly epitaph for each deleted file. Nothing leaves your
machine. No pip installs needed: standard library only.

Usage:
    python bin_ghoul.py                 # real Recycle Bin (falls back to demo if empty)
    python bin_ghoul.py --demo          # fake graveyard, safe for screen recordings
    python bin_ghoul.py --model qwen2.5:1.5b --port 8765
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OLLAMA = "http://localhost:11434"
MAX_SNIPPET = 400            # characters of a file's contents shown to the model
MAX_SNIPPET_FILE = 200_000   # never open text files bigger than this (bytes)
TEXT_EXT = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".html", ".json",
    ".csv", ".log", ".ini", ".yml", ".yaml", ".toml", ".xml", ".sql", ".sh", ".bat",
    ".c", ".cpp", ".h", ".java", ".go", ".rs", ".php", ".rb", ".cfg", ".env",
}

SYSTEM_PROMPT = (
    "You are the ghost of a file that was thrown into the Recycle Bin. "
    "Speak in the first person. Be spooky, dramatic and a little passive-aggressive "
    "about how you were discarded. If you are shown a fragment of your contents, "
    "mention something specific from it. Reply with exactly two short sentences. "
    "No hashtags, no emojis, no quotation marks."
)

DEMO = [
    ("index_FINAL_v2_final.js", "Desktop\\portfolio", "2 days ago", "48 KB",
     "// TODO: fix navbar\n// why is this broken??\nfunction toggleMenu() {\n  document.querySelector('.nav').classList.toggle('open')\n}"),
    ("unpaid_invoice_draft.txt", "Documents", "last Tuesday", "2 KB",
     "Hi, just following up on invoice #1042 which is now 60 days overdue. I would really appreciate..."),
    ("config_that_broke_everything.json", "Downloads", "yesterday", "1 KB",
     '{\n  "port": 80,\n  "debug": true,\n  "database": "prod-copy-do-not-use"\n}'),
    ("todo_list_2024.txt", "Desktop", "3 months ago", "1 KB",
     "1. learn guitar\n2. go to the gym\n3. finish the website\n4. call grandma"),
    ("secret_project_x.py", "Documents\\side-projects", "4 hours ago", "9 KB",
     "import random\n# this will definitely make me rich\ndef predict_lottery():\n    return random.randint(1, 49)"),
    ("new_music_idea_3am.txt", "Desktop", "last night", "1 KB",
     "chorus: your name is a candle in the window / I keep walking home to it..."),
    ("resume_v9_USE_THIS_ONE.txt", "Documents", "5 days ago", "3 KB",
     "Objective: to leverage my synergy in a fast-paced environment"),
]

ENTRIES = []          # list of dicts, index == id
GHOSTS = {}           # id -> cached epitaph
LOCK = threading.Lock()
SETTINGS = {"model": "llama3.2:1b", "demo": False}


# ---------- Reading the graveyard (read-only) ----------

PS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$s = New-Object -ComObject Shell.Application
$b = $s.NameSpace(10)
$out = @()
foreach ($i in $b.Items()) {
  $out += [pscustomobject]@{
    name = $i.Name; path = $i.Path; folder = [bool]$i.IsFolder
    where = $b.GetDetailsOf($i,1); deleted = $b.GetDetailsOf($i,2); size = $b.GetDetailsOf($i,3)
  }
}
ConvertTo-Json -InputObject @($out) -Compress
"""


def clean(text):
    # Windows pads date strings with invisible direction marks
    return (text or "").replace("\u200e", "").replace("\u200f", "").strip()


def list_windows():
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SCRIPT],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    raw = r.stdout.strip()
    items = json.loads(raw) if raw else []
    entries = []
    for it in items:
        name = clean(it.get("name"))
        path = it.get("path") or ""
        ext = Path(path).suffix
        if ext and not name.lower().endswith(ext.lower()):
            name += ext  # Explorer may hide extensions
        entries.append({
            "name": name, "where": clean(it.get("where")) or "somewhere unknown",
            "deleted": clean(it.get("deleted")) or "a time long forgotten",
            "size": clean(it.get("size")) or "unknown size",
            "path": path, "folder": bool(it.get("folder")),
        })
    return entries


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def list_posix():
    import datetime
    entries = []
    for base in (Path.home() / ".local/share/Trash/files", Path.home() / ".Trash"):
        if not base.is_dir():
            continue
        for p in base.iterdir():
            try:
                st = p.stat()
                entries.append({
                    "name": p.name, "where": "the Trash",
                    "deleted": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d %b %Y"),
                    "size": fmt_size(st.st_size), "path": str(p), "folder": p.is_dir(),
                })
            except OSError:
                continue
    return entries


def load_entries(force_demo):
    if not force_demo:
        try:
            found = list_windows() if os.name == "nt" else list_posix()
        except Exception as exc:  # PowerShell missing, timeout, bad JSON...
            print(f"  (could not read the Recycle Bin: {exc})")
            found = []
        if found:
            return found[:40], False
        print("  The bin is empty or unreadable, so the demo graveyard will rise instead.")
    demo = [{"name": n, "where": w, "deleted": d, "size": s, "snippet": sn, "folder": False}
            for n, w, d, s, sn in DEMO]
    return demo, True


def read_snippet(e):
    if "snippet" in e:
        return e["snippet"]
    p = e.get("path")
    if not p or e.get("folder") or Path(p).suffix.lower() not in TEXT_EXT:
        return ""
    try:
        if os.path.getsize(p) > MAX_SNIPPET_FILE:
            return ""
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(MAX_SNIPPET).strip()
    except OSError:
        return ""


# ---------- Talking to the local model ----------

def ollama_get(path, timeout=3):
    with urllib.request.urlopen(OLLAMA + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def status():
    st = {"ollama": False, "model": False, "modelName": SETTINGS["model"], "demo": SETTINGS["demo"]}
    try:
        tags = ollama_get("/api/tags")
        st["ollama"] = True
        want = SETTINGS["model"]
        st["model"] = any(m.get("name") == want or m.get("name") == want + ":latest"
                          for m in tags.get("models", []))
    except Exception:
        pass
    return st


def summon(e):
    snippet = read_snippet(e)
    lines = [
        f"File name: {e['name']}",
        f"It lived in: {e['where']}",
        f"Deleted: {e['deleted']}",
        f"Size: {e['size']}",
    ]
    if snippet:
        lines.append(f"A fragment of your contents:\n---\n{snippet}\n---")
    lines.append("Speak, spirit.")
    body = json.dumps({
        "model": SETTINGS["model"], "stream": False,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": "\n".join(lines)}],
        "options": {"temperature": 0.9, "num_predict": 90},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:  # first call loads the model
        text = json.loads(r.read().decode("utf-8"))["message"]["content"]
    return text.strip().strip('"')


# ---------- Tiny web server ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            self.send(200, PAGE.encode("utf-8"), "text/html")
        elif url.path == "/api/status":
            self.send(200, status())
        elif url.path == "/api/files":
            self.send(200, [{"id": i, "name": e["name"], "where": e["where"],
                             "deleted": e["deleted"], "size": e["size"]}
                            for i, e in enumerate(ENTRIES)])
        elif url.path == "/api/ghost":
            try:
                i = int(urllib.parse.parse_qs(url.query)["id"][0])
                entry = ENTRIES[i]
            except (KeyError, ValueError, IndexError):
                return self.send(404, {"error": "No such ghost."})
            with LOCK:
                cached = GHOSTS.get(i)
            if cached:
                return self.send(200, {"text": cached})
            try:
                text = summon(entry)
            except Exception:
                return self.send(503, {"error": "The spirit could not be reached. Is Ollama running with the model pulled?"})
            with LOCK:
                GHOSTS[i] = text
            self.send(200, {"text": text})
        else:
            self.send(404, {"error": "Not found"})


def main():
    global ENTRIES
    ap = argparse.ArgumentParser(description="Bin-Ghoul: ghostly epitaphs for your deleted files")
    ap.add_argument("--demo", action="store_true", help="use the fake graveyard instead of your real bin")
    ap.add_argument("--model", default="llama3.2:1b", help="Ollama model name (default: llama3.2:1b)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    SETTINGS["model"] = args.model
    print("\n  Bin-Ghoul: waking the Recycle Bin...")
    ENTRIES, SETTINGS["demo"] = load_entries(args.demo)
    print(f"  {len(ENTRIES)} spirits found ({'demo' if SETTINGS['demo'] else 'real bin'}). Model: {args.model}")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"  Open {url}  (Ctrl+C to lay the spirits to rest)\n")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("  Rest in peace.")
        sys.exit(0)


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bin-Ghoul</title>
<style>
:root{--ink:#2a2233;--gold:#ff7a1a;--mist:#efe4f5;--dim:#b3a4c2}
*{box-sizing:border-box}
html{background:#0b0713}
body{margin:0;min-height:100vh;color:var(--mist);font-family:Georgia,"Palatino Linotype",serif;line-height:1.6}
.scene{position:fixed;inset:0;z-index:-1;overflow:hidden;
  background:linear-gradient(180deg,#06030c 0%,#150b27 36%,#34173a 64%,#5a2330 84%,#2a0f17 100%)}
.star{position:absolute;border-radius:50%;background:#fff}
.moon{position:absolute;top:6vh;right:9vw;width:130px;height:130px;border-radius:50%;overflow:hidden;
  box-shadow:0 0 60px 20px rgba(255,120,40,.4),0 0 200px 70px rgba(255,90,20,.2)}
.moon div{width:100%;height:100%;filter:url(#moonf)}
.hills{position:absolute;left:0;bottom:0;width:100%;height:42vh}
.tree{position:absolute;bottom:0;height:62vh;stroke:#07040d;fill:none;stroke-linecap:round}
.fog{position:absolute;left:-10%;width:120%;bottom:0;height:46vh;opacity:.5;
  background:radial-gradient(ellipse 30% 30% at 20% 70%,rgba(220,205,235,.35),transparent),
    radial-gradient(ellipse 35% 25% at 62% 55%,rgba(220,205,235,.3),transparent),
    radial-gradient(ellipse 28% 30% at 90% 80%,rgba(220,205,235,.3),transparent);
  animation:drift 70s ease-in-out infinite alternate}
.fog.b{height:30vh;opacity:.4;animation-duration:95s;animation-direction:alternate-reverse}
@keyframes drift{to{transform:translateX(8%)}}
.vig{position:fixed;inset:0;pointer-events:none;box-shadow:inset 0 0 180px 40px rgba(0,0,0,.75)}
header{text-align:center;padding:60px 20px 8px}
h1{margin:0;font-size:clamp(2.4rem,7vw,4.2rem);font-style:italic;font-weight:400;
  color:#ff8a2a;text-shadow:0 0 22px rgba(255,122,26,.6),0 0 60px rgba(255,80,0,.35),0 3px 0 #000}
header p{margin:10px auto 0;max-width:46ch;color:var(--dim);font-size:1.05rem}
#banner{display:none;margin:18px auto 0;max-width:560px;padding:10px 16px;border:1px solid var(--gold);
  border-radius:10px;color:var(--gold);font-size:.95rem;background:rgba(0,0,0,.35)}
#banner code{background:rgba(0,0,0,.5);padding:2px 6px;border-radius:5px;color:var(--mist)}
.bar{display:flex;gap:14px;justify-content:center;align-items:center;margin:20px 0 0;color:var(--dim);font-size:.9rem}
button.all{font:inherit;font-style:italic;background:rgba(0,0,0,.35);color:var(--gold);
  border:1px solid var(--gold);border-radius:999px;padding:7px 20px;cursor:pointer}
button.all:hover,button.all:focus-visible{background:var(--gold);color:var(--ink);outline:none}
main{position:relative;margin-top:60px;padding:30px 0 150px;
  background:linear-gradient(180deg,transparent 0,rgba(8,14,10,.82) 120px,#070b08 100%)}
.grass{position:absolute;inset:0;filter:url(#grassf);opacity:.55;pointer-events:none}
#grid{position:relative;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
  gap:56px 34px;max-width:1120px;margin:0 auto;padding:0 26px;align-items:end}
.plot{position:relative;padding-bottom:6px;transition:filter .5s}
.plot::after{content:"";position:absolute;left:-8%;right:-8%;bottom:-8px;height:26px;z-index:-1;
  background:radial-gradient(ellipse,rgba(0,0,0,.75),transparent 70%)}
.plot.open{filter:drop-shadow(0 0 28px rgba(255,122,26,.6))}
.stone{all:unset;box-sizing:border-box;position:relative;display:block;width:100%;cursor:pointer;
  text-align:center;color:var(--ink);padding:62px 22px 30px;min-height:240px;overflow:hidden;background:#8d8596}
.stone:focus-visible{outline:3px solid var(--gold);outline-offset:5px}
.s0{border-radius:120px 120px 3px 3px}
.s1{border-radius:70px 70px 3px 3px;padding-top:44px}
.s2{clip-path:polygon(50% 0,100% 20%,100% 100%,0 100%,0 20%);padding-top:78px}
.s3{clip-path:polygon(7% 0,93% 0,100% 9%,100% 100%,0 100%,0 9%);padding-top:40px}
.rock{position:absolute;inset:0;z-index:0}
.stone::before{content:"";position:absolute;inset:0;z-index:1;pointer-events:none;border-radius:inherit;
  background:radial-gradient(60% 34% at 0 100%,rgba(66,98,52,.65),transparent 72%),
    radial-gradient(45% 26% at 100% 100%,rgba(58,88,44,.55),transparent 72%),
    radial-gradient(30% 18% at 70% 6%,rgba(60,90,50,.28),transparent 70%),
    linear-gradient(90deg,transparent 16%,rgba(0,0,0,.09) 18%,transparent 23%,transparent 66%,rgba(0,0,0,.08) 69%,transparent 76%)}
.stone::after{content:"";position:absolute;inset:0;z-index:1;pointer-events:none;border-radius:inherit;
  box-shadow:inset 3px 3px 7px rgba(255,255,255,.38),inset -10px -14px 26px rgba(0,0,0,.65)}
.stone>*{position:relative;z-index:2}
.carve{text-shadow:0 1px 0 rgba(255,255,255,.4),0 -1px 0 rgba(0,0,0,.45)}
.rip{display:block;font-style:italic;font-size:1.05rem;opacity:.8}
.name{display:block;margin:8px 0 6px;font-weight:bold;font-size:1.04rem;word-break:break-word}
.meta{display:block;font-size:.84rem;opacity:.85}
.epi{display:none;margin-top:16px;padding-top:14px;text-align:left;font-style:italic;font-size:1rem;
  border-top:2px groove rgba(60,50,70,.45)}
.plot.open .epi{display:block}
.plot.wait .epi{animation:pulse 1.4s ease-in-out infinite}
.plot.bad .epi{color:#7c2020;font-style:normal}
@keyframes pulse{50%{opacity:.35}}
.candle{display:none;position:absolute;right:-10px;bottom:2px;width:15px;height:34px;z-index:3;
  border-radius:3px 3px 1px 1px;background:linear-gradient(90deg,#c9b98f,#f4ead0 45%,#b5a57c)}
.plot.open .candle{display:block}
.candle::before{content:"";position:absolute;left:3px;top:-20px;width:9px;height:19px;
  border-radius:50% 50% 50% 50%/62% 62% 38% 38%;transform-origin:50% 100%;
  background:radial-gradient(ellipse at 50% 70%,#fff6c8 0,#ffd166 35%,#f2a33a 65%,rgba(242,163,58,0) 100%);
  box-shadow:0 0 18px 6px rgba(242,163,58,.55);animation:flick 1.3s ease-in-out infinite alternate}
@keyframes flick{0%{transform:scale(1,1) rotate(-2deg)}50%{transform:scale(.92,1.08) rotate(2deg)}100%{transform:scale(1.04,.96) rotate(-1deg)}}
#fx{position:fixed;inset:0;width:100%;height:100%;z-index:1;pointer-events:none;mix-blend-mode:screen;opacity:.85}
header,.bar{position:relative;z-index:2}
.vig{z-index:3}
.grain{position:fixed;inset:0;pointer-events:none;z-index:3;filter:url(#grainf);opacity:.3;mix-blend-mode:overlay}
#webs svg{position:fixed;top:0;width:min(30vw,200px);fill:none;stroke:rgba(235,225,245,.4);stroke-width:1;z-index:4;pointer-events:none}
#bats{position:fixed;inset:0;pointer-events:none;z-index:1}
.bat{position:absolute;left:-60px;width:44px;color:#0a0512;filter:drop-shadow(0 0 3px rgba(255,140,60,.4));animation:fly 26s linear infinite}
.bat svg{display:block;fill:currentColor;transform-origin:50% 60%;animation:flap .22s ease-in-out infinite alternate}
@keyframes fly{0%{transform:translate(0,0)}25%{transform:translate(30vw,-3vh)}50%{transform:translate(60vw,4vh)}75%{transform:translate(90vw,-4vh)}100%{transform:translate(122vw,2vh)}}
@keyframes flap{to{transform:scaleY(.45)}}
.eyes{position:absolute;width:32px;height:10px;opacity:0;animation:blink 11s infinite;filter:drop-shadow(0 0 5px #ff3a1a);
  background:radial-gradient(circle at 6px 5px,#ff5a2a 0 3px,transparent 4px),radial-gradient(circle at 26px 5px,#ff5a2a 0 3px,transparent 4px)}
@keyframes blink{0%,60%{opacity:0}62%,70%{opacity:1}72%{opacity:0}74%,80%{opacity:1}82%,100%{opacity:0}}
@keyframes ttl{0%,91%,95%,98%,100%{opacity:1}92%{opacity:.5}93%{opacity:1}96%{opacity:.65}}
h1{animation:ttl 7s infinite}
.plot::before{content:"";position:absolute;left:-62%;bottom:-4px;width:78%;height:30px;z-index:-1;
  background:linear-gradient(270deg,rgba(0,0,0,.75),transparent);transform:skewX(-55deg);transform-origin:100% 100%;filter:blur(2px)}
.crack{position:absolute;bottom:0;width:22%;height:50%;pointer-events:none}
.crack polyline{fill:none;stroke:rgba(10,6,14,.6);stroke-width:1.3;stroke-linejoin:round}
.pumpkin{position:absolute;left:-32px;bottom:-5px;width:58px;z-index:3;pointer-events:none;animation:pflick 2.4s ease-in-out infinite alternate}
.pumpkin svg{display:block}
@keyframes pflick{from{filter:drop-shadow(0 0 7px rgba(255,110,20,.55))}to{filter:drop-shadow(0 0 17px rgba(255,150,40,.95))}}
header h1{animation:reveal 2.4s ease-out both,ttl 7s 3.2s infinite}
header p{animation:rise2 1.8s .9s ease-out both}
@keyframes reveal{from{opacity:0;filter:blur(14px);letter-spacing:.16em}to{opacity:1;filter:blur(0);letter-spacing:.01em}}
@keyframes rise2{from{opacity:0;translate:0 12px}to{opacity:1;translate:0 0}}
.moon{animation:breathe 9s ease-in-out infinite alternate}
@keyframes breathe{to{box-shadow:0 0 84px 30px rgba(255,120,40,.52),0 0 250px 90px rgba(255,90,20,.27)}}
.tree{transform-origin:50% 100%;animation:sway 10s ease-in-out infinite alternate}
#treeR{animation-delay:-5s}
@keyframes sway{from{rotate:-.7deg}to{rotate:.9deg}}
.manor{position:absolute;left:64%;bottom:25vh;width:210px;fill:#0a0612}
.manor .w{fill:#ffb040;filter:drop-shadow(0 0 4px #ff9020);animation:win 3.6s infinite}
@keyframes win{0%,100%{opacity:1}45%{opacity:.5}50%{opacity:1}72%{opacity:.3}}
.grass{filter:url(#earthf);opacity:.7}
main{background:linear-gradient(180deg,transparent 0,rgba(10,8,12,.85) 110px,#07060a 100%)}
.plot{animation:rise 1s cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--i,0)*90ms + .5s)}
@keyframes rise{from{translate:0 50px;opacity:0}to{translate:0 0;opacity:1}}
.plot:hover{filter:drop-shadow(0 0 16px rgba(255,122,26,.4))}
.plot.open:hover{filter:drop-shadow(0 0 28px rgba(255,122,26,.6))}
.stone{transition:translate .3s}
.stone:hover{translate:0 -4px}
.tuft{position:absolute;left:-14%;width:128%;bottom:-9px;height:38px;z-index:3;pointer-events:none;transform-origin:50% 100%;animation:grass 5s ease-in-out infinite alternate}
@keyframes grass{from{transform:skewX(-2.2deg)}to{transform:skewX(2.2deg)}}
.fgrass{position:fixed;left:-2%;bottom:-6px;width:104%;height:84px;z-index:2;pointer-events:none}
.fgrass g{transform-box:fill-box;transform-origin:50% 100%;animation:grass 6s ease-in-out infinite alternate}
.ember{position:fixed;bottom:-10px;border-radius:50%;background:#ffb25c;box-shadow:0 0 8px 2px rgba(255,122,26,.8);z-index:2;pointer-events:none;opacity:0;animation:ember linear infinite}
@keyframes ember{0%{translate:0 0;opacity:0}10%{opacity:.9}100%{translate:var(--dx,40px) -105vh;opacity:0}}
.wisp{position:absolute;left:50%;top:-10px;width:46px;margin-left:-23px;z-index:4;pointer-events:none;animation:ascend 3.4s ease-out forwards;filter:drop-shadow(0 0 10px rgba(190,220,255,.8))}
@keyframes ascend{0%{translate:0 20px;opacity:0;scale:.6}15%{opacity:.9}100%{translate:18px -150px;opacity:0;scale:1.2}}
.flash{position:fixed;inset:0;z-index:3;pointer-events:none;opacity:0;mix-blend-mode:screen;background:radial-gradient(ellipse at 70% 10%,rgba(190,170,255,.55),rgba(120,100,200,.2) 60%,transparent)}
.flash.go{animation:zap .9s}
@keyframes zap{0%,100%{opacity:0}8%{opacity:1}16%{opacity:.1}26%{opacity:.8}60%{opacity:.15}}
#foot{z-index:5}
@media (prefers-reduced-motion:reduce){.ember,.flash,.tuft,.fgrass g,.moon,.tree,.plot,.wisp,.manor .w,header p,header h1{animation:none}.ember{display:none}}
footer{position:fixed;left:0;right:0;bottom:0;text-align:center;padding:10px;font-size:.82rem;color:var(--dim);
  background:linear-gradient(0deg,#070b08 45%,transparent)}
@media (prefers-reduced-motion:reduce){.fog,.plot.wait .epi,.candle::before,.bat,.bat svg,.eyes,.pumpkin,h1{animation:none}.bat{display:none}}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<filter id="rock0" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".03 .07" numOctaves="5" seed="3"/><feDiffuseLighting lighting-color="#d4cdda" surfaceScale="3.4"><feDistantLight azimuth="225" elevation="50"/></feDiffuseLighting></filter>
<filter id="rock1" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".05 .05" numOctaves="5" seed="11"/><feDiffuseLighting lighting-color="#cfd0d6" surfaceScale="3"><feDistantLight azimuth="225" elevation="52"/></feDiffuseLighting></filter>
<filter id="rock2" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".02 .09" numOctaves="6" seed="21"/><feDiffuseLighting lighting-color="#d6cfc6" surfaceScale="3.6"><feDistantLight azimuth="225" elevation="48"/></feDiffuseLighting></filter>
<filter id="moonf" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".045" numOctaves="4" seed="2"/><feDiffuseLighting lighting-color="#ffb25c" surfaceScale="4"><feDistantLight azimuth="300" elevation="58"/></feDiffuseLighting></filter>
<filter id="grassf" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".5 .03" numOctaves="3" seed="4"/><feColorMatrix values="0 0 0 0 .1  0 0 0 0 .2  0 0 0 0 .12  0 0 0 1.5 -.4"/></filter>
<filter id="earthf" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".06" numOctaves="4" seed="5"/><feDiffuseLighting lighting-color="#3a2f3d" surfaceScale="1.4"><feDistantLight azimuth="235" elevation="60"/></feDiffuseLighting></filter>
<filter id="grainf" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="2" seed="9"/><feColorMatrix type="saturate" values="0"/></filter>
</defs></svg>

<div class="scene" aria-hidden="true">
  <div id="stars"></div>
  <div class="moon"><div></div></div>
  <svg class="tree" id="treeL" style="left:-2vw" viewBox="0 0 320 420"></svg>
  <svg class="tree" id="treeR" style="right:-3vw;transform:scaleX(-1)" viewBox="0 0 320 420"></svg>
  <div class="eyes" style="left:7vw;bottom:34vh"></div><div class="eyes" style="right:8vw;bottom:24vh;animation-delay:4s"></div>
  <svg class="manor" viewBox="0 0 200 150" aria-hidden="true"><path d="M22 150V70L40 28l18 42v80zM58 150V92h92v58zM52 92l52-34 52 34zM150 150V78l14-34 14 34v72zM118 70V46h8v28z"/><rect class="w" x="36" y="84" width="8" height="12"/><rect class="w" x="76" y="110" width="9" height="14" style="animation-delay:-1.4s"/><rect class="w" x="118" y="110" width="9" height="14" style="animation-delay:-2.2s"/><rect class="w" x="160" y="96" width="7" height="11" style="animation-delay:-.6s"/></svg>
  <svg class="hills" viewBox="0 0 1440 300" preserveAspectRatio="none">
    <path d="M0 170 C200 110 380 200 600 150 S1000 90 1200 160 S1400 140 1440 130 V300 H0Z" fill="#1a1028"/>
    <path d="M0 230 C240 180 420 250 700 210 S1100 170 1440 220 V300 H0Z" fill="#0f0a1a"/>
  </svg>
  <div class="fog"></div><div class="fog b"></div>
</div>
<div class="vig" aria-hidden="true"></div>
<canvas id="fx" aria-hidden="true"></canvas>
<div class="grain" aria-hidden="true"></div>
<div id="webs" aria-hidden="true"></div>
<div id="bats" aria-hidden="true"></div>

<header>
  <h1>The Bin-Ghoul</h1>
  <p>Everything you deleted is still down here, and it has opinions.</p>
  <div id="banner"></div>
</header>
<div class="bar"><span id="count"></span><button class="all" id="all">Wake them all</button></div>
<main><div class="grass"></div><div id="grid" aria-live="polite"></div></main>
<footer id="foot">Read-only. Every epitaph is written by a model running on your own computer.</footer>

<script>
let seed = 7;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

// stars
const sky = document.getElementById('stars');
for (let i = 0; i < 90; i++){
  const s = document.createElement('div'); s.className = 'star';
  const z = rnd() * 2 + .6;
  s.style.cssText = `left:${rnd()*100}%;top:${rnd()*55}%;width:${z}px;height:${z}px;opacity:${.25 + rnd()*.6}`;
  sky.append(s);
}

// shader fog (WebGL); the CSS fog stays as a fallback
(function(){
  const c = document.getElementById('fx'), gl = c.getContext('webgl');
  if (!gl) { c.remove(); return; }
  const fs = `precision mediump float;uniform vec2 r;uniform vec2 m;uniform float t;
  float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
  float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
    return mix(mix(h(i),h(i+vec2(1.,0.)),f.x),mix(h(i+vec2(0.,1.)),h(i+vec2(1.,1.)),f.x),f.y);}
  float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p=p*2.03+vec2(1.7,9.2);a*=.5;}return v;}
  void main(){
    vec2 uv=gl_FragCoord.xy/r;float asp=r.x/r.y;vec2 q=vec2(uv.x*asp,uv.y)*2.4;
    float w=fbm(q*.8+vec2(t*.02,-t*.01));
    float f=fbm(q+vec2(t*.04,0.)+w*1.6);
    float ground=pow(1.-uv.y,1.6);
    float fog=smoothstep(.38,.9,f)*(.15+.85*ground);
    float d=distance(uv*vec2(asp,1.),m*vec2(asp,1.));
    float glow=exp(-d*2.8)*(.7+.3*n(q*2.+t*.1));
    vec3 tint=mix(vec3(.42,.22,.55),vec3(1.,.45,.12),smoothstep(.55,1.,f)*(1.-uv.y));
    tint+=vec3(1.,.5,.15)*glow*.9;
    float g=(h(gl_FragCoord.xy+fract(t)*97.)-.5)*.06;
    gl_FragColor=vec4(tint*fog*.8+vec3(g)*fog,1.);}`;
  const mk = (type, code) => { const s = gl.createShader(type); gl.shaderSource(s, code); gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null; };
  const vs = mk(gl.VERTEX_SHADER, 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}');
  const fsh = mk(gl.FRAGMENT_SHADER, fs);
  if (!vs || !fsh) { c.remove(); return; }
  const prog = gl.createProgram(); gl.attachShader(prog, vs); gl.attachShader(prog, fsh); gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { c.remove(); return; }
  gl.useProgram(prog);
  document.querySelectorAll('.fog').forEach(f => f.remove());
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const ur = gl.getUniformLocation(prog, 'r'), ut = gl.getUniformLocation(prog, 't'), um = gl.getUniformLocation(prog, 'm');
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  function size(){ c.width = (innerWidth * .5) | 0; c.height = (innerHeight * .5) | 0; gl.viewport(0, 0, c.width, c.height); }
  size(); addEventListener('resize', () => { size(); if (still) draw(0); });
  function draw(ms){
    gl.uniform2f(ur, c.width, c.height); gl.uniform1f(ut, ms / 1000 + 20);
    gl.uniform2f(um, 1 - (.09 * innerWidth + 65) / innerWidth, 1 - (.06 * innerHeight + 65) / innerHeight);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    if (!still) requestAnimationFrame(draw);
  }
  draw(0);
})();

// bats crossing the moon
const BAT = '<svg viewBox="0 0 40 20"><path d="M20 6C18 2 14 1 8 3C10 5 9 8 12 9C13 7 15 7 16 9C17 8 19 8 20 12C21 8 23 8 24 9C25 7 27 7 28 9C31 8 30 5 32 3C26 1 22 2 20 6Z"/></svg>';
[[6, 3, 1], [15, 12, .8], [36, 20, .7]].forEach(([top, delay, sc]) => {
  const b = document.createElement('div'); b.className = 'bat'; b.innerHTML = BAT;
  b.style.cssText = `top:${top}vh;animation-delay:${delay}s;width:${44 * sc}px`;
  document.getElementById('bats').append(b);
});

// cobwebs
function web(){
  const rays = 6, R = [34, 68, 102, 136, 170]; let d = '';
  const pt = (r, k) => [r * Math.cos(k * Math.PI / 2 / (rays - 1)), r * Math.sin(k * Math.PI / 2 / (rays - 1))];
  for (let k = 0; k < rays; k++){ const [x, y] = pt(170, k); d += `M0 0L${x.toFixed(1)} ${y.toFixed(1)}`; }
  for (const r of R) for (let k = 0; k < rays - 1; k++){
    const a = pt(r, k), b = pt(r, k + 1);
    d += `M${a[0].toFixed(1)} ${a[1].toFixed(1)}Q${((a[0] + b[0]) * .4).toFixed(1)} ${((a[1] + b[1]) * .4).toFixed(1)} ${b[0].toFixed(1)} ${b[1].toFixed(1)}`;
  }
  return `<svg viewBox="0 0 170 170"><path d="${d}"/></svg>`;
}
document.getElementById('webs').innerHTML = web() + web().replace('<svg', '<svg style="right:0;transform:scaleX(-1)"');

// grass blades, embers, lightning, wisps
function blades(n, w, h, minH, maxH, fills){
  let s = '';
  for (let i = 0; i < n; i++){
    const x = rnd() * w, hh = minH + rnd() * (maxH - minH), lean = (rnd() - .5) * hh * .7, bw = 1.2 + rnd() * 2;
    s += `<path d="M${x.toFixed(1)} ${h}Q${(x + lean * .4).toFixed(1)} ${(h - hh * .6).toFixed(1)} ${(x + lean).toFixed(1)} ${(h - hh).toFixed(1)}Q${(x + lean * .4 + bw).toFixed(1)} ${(h - hh * .55).toFixed(1)} ${(x + bw * 1.6).toFixed(1)} ${h}Z" fill="${fills[(rnd() * fills.length) | 0]}"/>`;
  }
  return s;
}
function tuft(){
  const d = document.createElement('div'); d.className = 'tuft'; d.style.animationDelay = (-rnd() * 5).toFixed(1) + 's';
  d.innerHTML = `<svg viewBox="0 0 130 40" preserveAspectRatio="none" width="100%" height="100%">${blades(26, 130, 40, 12, 34, ['#0b150e', '#14241a', '#1d3320', '#0f1c13'])}</svg>`;
  return d;
}
const fgw = document.createElement('div');
fgw.innerHTML = `<svg class="fgrass" aria-hidden="true" viewBox="0 0 1440 84" preserveAspectRatio="none"><g style="animation-delay:-1s">${blades(150, 1440, 84, 26, 70, ['#04060a', '#070a0e'])}</g><g style="animation-delay:-3s">${blades(110, 1440, 84, 18, 52, ['#0a1210', '#0d1a12'])}</g></svg>`;
document.body.append(fgw.firstChild);
for (let i = 0; i < 22; i++){
  const e = document.createElement('div'); e.className = 'ember'; const z = 2 + rnd() * 3;
  e.style.cssText = `left:${rnd() * 100}%;--dx:${((rnd() - .5) * 160).toFixed(0)}px;animation-duration:${(9 + rnd() * 10).toFixed(1)}s;animation-delay:${(-rnd() * 18).toFixed(1)}s;width:${z}px;height:${z}px`;
  document.body.append(e);
}
const flash = document.createElement('div'); flash.className = 'flash'; document.body.append(flash);
if (!matchMedia('(prefers-reduced-motion: reduce)').matches){
  (function zap(first){
    setTimeout(() => { flash.classList.remove('go'); void flash.offsetWidth; flash.classList.add('go'); zap(false); }, first ? 7000 : 16000 + Math.random() * 22000);
  })(true);
}
const GHOST = '<svg viewBox="0 0 40 50"><path d="M20 2C10 2 4 10 4 20v26l6-5 5 5 5-5 5 5 5-5 6 5V20C36 10 30 2 20 2z" fill="rgba(235,245,255,.88)"/><circle cx="14" cy="20" r="3" fill="#1a1325"/><circle cx="26" cy="20" r="3" fill="#1a1325"/><ellipse cx="20" cy="29" rx="3" ry="4" fill="#1a1325"/></svg>';
function spawnWisp(plot){ const w = document.createElement('div'); w.className = 'wisp'; w.innerHTML = GHOST; plot.append(w); setTimeout(() => w.remove(), 3600); }

// cracks and jack-o'-lanterns
const SVGNS = 'http://www.w3.org/2000/svg';
function crack(){
  const s = document.createElementNS(SVGNS, 'svg');
  s.setAttribute('viewBox', '0 0 60 100'); s.setAttribute('preserveAspectRatio', 'none'); s.setAttribute('class', 'crack');
  let x = 10 + rnd() * 40, y = 0; const pts = [];
  for (let i = 0; i < 9; i++){ pts.push(x.toFixed(1) + ',' + y.toFixed(1)); x += (rnd() - .5) * 18; y += 8 + rnd() * 8; }
  const pl = document.createElementNS(SVGNS, 'polyline'); pl.setAttribute('points', pts.join(' ')); s.append(pl);
  s.style.left = (8 + rnd() * 60) + '%';
  return s;
}
const PUMPKIN = '<svg viewBox="0 0 60 52"><path d="M30 8c-6-4-16-2-20 6-4 8-2 22 8 28 6 3 10 2 12 1 2 1 6 2 12-1 10-6 12-20 8-28-4-8-14-10-20-6z" fill="#e8651a"/><path d="M30 8c-3 8-3 26 0 36M20 12c-4 8-4 24 0 30M40 12c4 8 4 24 0 30" stroke="#a8430c" stroke-width="2" fill="none"/><rect x="28" y="2" width="5" height="9" rx="2" fill="#4a5a2a"/><path d="M16 22l8 2-6 6zM44 22l-8 2 6 6zM18 36q12 8 24 0l-4-4-4 3-4-3-4 3-4-3z" fill="#ffd166"/></svg>';
function pumpkin(){ const d = document.createElement('div'); d.className = 'pumpkin'; d.innerHTML = PUMPKIN; return d; }

// dead trees (generated branches)
function branches(x, y, len, ang, w, d, out){
  if (d === 0 || len < 6) return;
  const x2 = x + Math.sin(ang) * len, y2 = y - Math.cos(ang) * len;
  out.push(`<line x1="${x.toFixed(1)}" y1="${y.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke-width="${w.toFixed(1)}"/>`);
  const n = d > 2 && rnd() > .4 ? 3 : 2;
  for (let i = 0; i < n; i++){
    const a = ang + (i - (n - 1) / 2) * (.5 + rnd() * .35) + (rnd() - .5) * .25;
    branches(x2, y2, len * (.68 + rnd() * .1), a, w * .68, d - 1, out);
  }
}
for (const id of ['treeL', 'treeR']){
  const out = []; branches(160, 420, 95, id === 'treeL' ? .08 : -.05, 13, 8, out);
  document.getElementById(id).innerHTML = out.join('');
}

// graveyard
const grid = document.getElementById('grid');
const plots = [];
function el(tag, cls, text){ const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }

function type(node, text){
  node.textContent = '';
  if (matchMedia('(prefers-reduced-motion: reduce)').matches){ node.textContent = text; return; }
  let i = 0;
  const t = setInterval(() => { node.textContent = text.slice(0, ++i); if (i >= text.length) clearInterval(t); }, 22);
}

async function wake(plot, id){
  if (plot.dataset.done){ plot.classList.toggle('open'); return; }
  if (plot.dataset.busy) return;
  plot.dataset.busy = '1';
  const epi = plot.querySelector('.epi');
  plot.classList.add('open', 'wait');
  epi.textContent = 'The spirit stirs...';
  try {
    const r = await fetch('/api/ghost?id=' + id);
    const j = await r.json();
    plot.classList.remove('wait');
    if (!r.ok) throw new Error(j.error);
    plot.dataset.done = '1';
    spawnWisp(plot);
    type(epi, j.text);
  } catch (e){
    plot.classList.remove('wait'); plot.classList.add('bad');
    epi.textContent = e.message || 'The spirit could not be reached.';
  } finally { delete plot.dataset.busy; }
}

async function boot(){
  const st = await (await fetch('/api/status')).json();
  const banner = document.getElementById('banner');
  if (!st.ollama){
    banner.style.display = 'block';
    banner.textContent = 'Ollama is not running. Start the Ollama app, then refresh this page.';
  } else if (!st.model){
    banner.style.display = 'block';
    banner.append('The model is missing. In a terminal, run ');
    banner.append(el('code', '', 'ollama pull ' + st.modelName));
    banner.append(' then refresh.');
  }
  if (st.demo) document.getElementById('foot').textContent = 'Demo graveyard: these files are fictional. Epitaphs are written by a model running on your own computer.';
  const files = await (await fetch('/api/files')).json();
  document.getElementById('count').textContent = files.length + ' spirits restless in the bin';
  files.forEach((f, i) => {
    const plot = el('div', 'plot');
    plot.style.transform = `rotate(${((rnd() - .5) * 3).toFixed(2)}deg)`;
    plot.style.marginTop = `${Math.round(rnd() * 26)}px`;
    const s = el('button', 'stone s' + (i % 4)); s.type = 'button';
    const rock = el('div', 'rock'); rock.style.filter = `url(#rock${i % 3})`;
    const body = el('span', 'carve');
    body.style.display = 'block';
    body.append(el('span', 'rip', 'Here lies'), el('span', 'name', f.name),
                el('span', 'meta', 'Deleted ' + f.deleted),
                el('span', 'meta', f.size + ' from ' + f.where), el('div', 'epi'));
    s.append(rock, body);
    s.append(crack());
    plot.append(s, el('div', 'candle'));
    if (i % 3 === 1) plot.append(pumpkin());
    plot.append(tuft());
    plot.style.setProperty('--i', Math.min(i, 14));
    s.addEventListener('click', () => wake(plot, f.id));
    grid.append(plot); plots.push([plot, f.id]);
  });
  document.getElementById('all').addEventListener('click', async () => {
    for (const [p, id] of plots){ if (!p.dataset.done) await wake(p, id); }
  });
}
boot();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
