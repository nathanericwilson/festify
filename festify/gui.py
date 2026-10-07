"""Small local web UI for festify: `python -m festify.gui` (or double-click Festify.bat).

Serves one page on 127.0.0.1 only and runs festify as a subprocess, streaming its output.
Standard library only.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import ROOT

HOST, PORT = "127.0.0.1", 8765
EVENTS_FILE = "events.json"
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self.exit: int | None = None
        self.logfile: Path | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


JOB = Job()


def build_args(opts: dict, image_paths: list[str]) -> list[str]:
    """Translate the form into festify CLI arguments."""
    args: list[str] = []
    for pin in opts.get("pins", []):
        if pin.strip():
            args += ["--pin", pin.strip()]
    if opts.get("resume"):
        args += ["--events-file", EVENTS_FILE]
    else:
        inputs = [l.strip() for l in opts.get("links", []) if l.strip()] + image_paths
        args += inputs
        if inputs:
            args += ["--save-events", EVENTS_FILE]  # so "resume last run" works next time
    if opts.get("free", True):
        args.append("--free")
    for flag in ("dry_run", "refresh", "no_web"):
        if opts.get(flag):
            args.append("--" + flag.replace("_", "-"))
    tracks = int(opts.get("tracks") or 3)
    if tracks != 3:
        args += ["--tracks", str(max(1, min(tracks, 10)))]
    return args


def save_uploads(images: list[dict]) -> list[str]:
    if not images:
        return []
    folder = Path(tempfile.mkdtemp(prefix="festify_upload_"))
    paths = []
    for i, img in enumerate(images):
        ext = Path(img.get("name", "")).suffix.lower()
        if ext not in IMG_EXT:
            ext = ".png"
        p = folder / f"slide_{i:02d}{ext}"
        p.write_bytes(base64.b64decode(img["data"].split(",", 1)[-1]))
        paths.append(str(p))
    return [str(folder)] if paths else []  # one folder = one post, so carousel slides stay together


def start(opts: dict) -> str | None:
    with JOB.lock:
        if JOB.running:
            return "A run is already in progress."
        args = build_args(opts, save_uploads(opts.get("images", [])))
        has_pins = any(p.strip() for p in opts.get("pins", []))
        if "--save-events" not in args and not opts.get("resume") and not has_pins:
            return "Add at least one Instagram link or screenshot (or tick 'Resume last run')."
        JOB.lines, JOB.exit = [f"$ festify {' '.join(args)}"], None
        (ROOT / "logs").mkdir(exist_ok=True)
        JOB.logfile = ROOT / "logs" / f"festify_{time.strftime('%Y%m%d_%H%M%S')}.log"
        JOB.logfile.write_text(JOB.lines[0] + "\n", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        JOB.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "festify", *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    threading.Thread(target=_pump, args=(JOB.proc,), daemon=True).start()
    return None


def _pump(proc: subprocess.Popen):
    with open(JOB.logfile, "a", encoding="utf-8") as f:  # every run is also saved under logs/
        for line in proc.stdout:
            JOB.lines.append(line.rstrip("\n"))
            f.write(line)
            f.flush()
        JOB.exit = proc.wait()
        JOB.lines.append(f"[finished with exit code {JOB.exit}]")
        f.write(JOB.lines[-1] + "\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the console quiet
        pass

    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE, "text/html")
        m = re.match(r"/log\?from=(\d+)", self.path)
        if m:
            i = int(m.group(1))
            return self._send(200, {"lines": JOB.lines[i:], "next": len(JOB.lines),
                                    "running": JOB.running, "exit": JOB.exit, "logfile": str(JOB.logfile or "")})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.headers.get("Origin") not in (None, f"http://{HOST}:{PORT}"):
            return self._send(403, {"error": "forbidden"})  # only this page may start runs
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        if self.path == "/run":
            err = start(json.loads(body or b"{}"))
            return self._send(400 if err else 200, {"error": err} if err else {"ok": True})
        if self.path == "/stop":
            if JOB.running:
                JOB.proc.terminate()
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Festify</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5f6673;--line:#e3e6ea;--accent:#1db954;--accent-ink:#06210f;--log:#0f1115;--logink:#d7dde6;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#0e1013;--card:#171a1f;--ink:#e8ebf0;--muted:#9aa3af;--line:#2a2f37;--log:#0a0b0d;--logink:#cfd6e0;--warn:#f59e0b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:22px 24px 6px;max-width:1150px;margin:auto}h1{margin:0;font-size:26px;letter-spacing:-.02em}h1 span{color:var(--accent)}
header p{margin:4px 0 0;color:var(--muted)}
main{max-width:1150px;margin:auto;padding:16px 24px 32px;display:grid;grid-template-columns:minmax(300px,420px) 1fr;gap:20px}
@media (max-width:860px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
label.h{display:block;font-weight:600;margin:14px 0 6px}label.h:first-child{margin-top:0}
textarea,input[type=number]{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--bg);color:var(--ink);font:inherit}
textarea{min-height:92px;resize:vertical}.hint{color:var(--muted);font-size:13px;margin-top:4px}
.drop{border:1.5px dashed var(--line);border-radius:10px;padding:14px;text-align:center;color:var(--muted);cursor:pointer}
.drop.on{border-color:var(--accent);color:var(--ink)}
.opts{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px}.opts label{display:flex;gap:8px;align-items:center;cursor:pointer}
.row{display:flex;gap:10px;margin-top:18px}
button{border:0;border-radius:999px;padding:11px 20px;font:600 15px system-ui;cursor:pointer}
#go{background:var(--accent);color:var(--accent-ink);flex:1}#go:disabled{opacity:.5;cursor:default}
#stop,button.small{background:transparent;border:1px solid var(--line);color:var(--ink)}
button.small{margin-left:auto;padding:5px 14px;font-size:13px}
details{margin-top:14px}summary{cursor:pointer;color:var(--muted)}
#status{display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:10px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--muted)}.dot.run{background:var(--accent);animation:p 1s infinite}
@keyframes p{50%{opacity:.3}}
#playlists a{display:block;padding:10px 12px;margin-bottom:8px;border:1px solid var(--line);border-radius:10px;color:var(--ink);text-decoration:none}
#playlists a:hover{border-color:var(--accent)}#playlists small{color:var(--muted)}
pre{background:var(--log);color:var(--logink);border-radius:12px;padding:14px;height:56vh;overflow:auto;margin:0;font:12.5px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap}
pre a{color:#4ade80}.stop{color:#fbbf24;font-weight:700}
</style></head><body>
<header><h1>Fest<span>ify</span></h1><p>Concert posters &rarr; Spotify playlists, one per event.</p></header>
<main>
<section class="card">
  <label class="h" for="links">Instagram links</label>
  <textarea id="links" placeholder="https://www.instagram.com/p/...&#10;one per line"></textarea>
  <label class="h">Screenshots</label>
  <div class="drop" id="drop">Drop images here or click to choose<br><span class="hint">All images you add count as one post</span></div>
  <input type="file" id="files" accept="image/*" multiple hidden>
  <div id="filelist" class="hint"></div>
  <label class="h">Options</label>
  <div class="opts">
    <label><input type="checkbox" id="free" checked> Free mode (Gemini)</label>
    <label><input type="checkbox" id="dry_run"> Dry run (preview)</label>
    <label><input type="checkbox" id="refresh"> Refresh existing playlists</label>
    <label><input type="checkbox" id="no_web"> No web search</label>
    <label><input type="checkbox" id="resume"> Resume last run</label>
    <label>Songs/artist <input type="number" id="tracks" value="3" min="1" max="10" style="width:64px"></label>
  </div>
  <details><summary>Fix a wrong band (pin)</summary>
    <textarea id="pins" placeholder="Skrude=https://open.spotify.com/artist/4zla...&#10;one per line"></textarea>
    <div class="hint">Tip: pin, then tick "Resume last run" + "Refresh existing playlists".</div>
  </details>
  <div class="row"><button id="go">Build playlists</button><button id="stop">Stop</button></div>
  <div class="hint" style="margin-top:10px">Choosing between same-name bands interactively (--ask) needs the terminal.</div>
</section>
<section>
  <div class="card" style="margin-bottom:16px"><div id="status"><span class="dot" id="dot"></span><span id="st">Idle</span><button id="copy" class="small">Copy log</button></div>
    <div id="playlists"><span class="hint">Playlist links will appear here.</span></div></div>
  <pre id="log"></pre>
  <div class="hint" id="logpath" style="margin-top:6px"></div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);let images=[],from=0,timer=null,seen=new Set();
const drop=$("drop"),files=$("files");
drop.onclick=()=>files.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add("on")};drop.ondragleave=()=>drop.classList.remove("on");
drop.ondrop=e=>{e.preventDefault();drop.classList.remove("on");add(e.dataTransfer.files)};files.onchange=()=>add(files.files);
function add(list){[...list].forEach(f=>{const r=new FileReader();r.onload=()=>{images.push({name:f.name,data:r.result});show()};r.readAsDataURL(f)})}
function show(){$("filelist").textContent=images.length?images.length+" image(s): "+images.map(i=>i.name).join(", ")+"  (click Build to use, reload page to clear)":""}
const esc=s=>s.replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const linkify=s=>esc(s).replace(/(https:\/\/open\.spotify\.com\/[^\s)]+)/g,'<a href="$1" target="_blank" rel="noopener">$1</a>');
let lastEvent="";
function addLines(lines){const log=$("log");const atEnd=log.scrollTop+log.clientHeight>=log.scrollHeight-20;
  for(const l of lines){const m=l.match(/^(.+?) – (\S+)\s+\[/);if(m&&!l.startsWith(" "))lastEvent=l.split("   [")[0];
    const p=l.match(/^Playlist: (https:\/\/open\.spotify\.com\/playlist\/\w+)(.*)$/);
    if(p&&!seen.has(p[1])){seen.add(p[1]);if(seen.size===1)$("playlists").innerHTML="";
      $("playlists").insertAdjacentHTML("beforeend",`<a href="${p[1]}" target="_blank" rel="noopener">${esc(lastEvent||"Playlist")}<br><small>${p[1]}${esc(p[2])}</small></a>`)}
    log.insertAdjacentHTML("beforeend",(l.startsWith("STOPPED")?'<span class="stop">'+linkify(l)+"</span>":linkify(l))+"\n")}
  if(atEnd)log.scrollTop=log.scrollHeight}
async function poll(){const r=await (await fetch("/log?from="+from)).json();from=r.next;addLines(r.lines);
  $("dot").className="dot"+(r.running?" run":"");$("st").textContent=r.running?"Running…":(r.exit===null?"Idle":(r.exit===0?"Done":"Stopped (see log)"));
  if(r.logfile)$("logpath").textContent="Log saved to "+r.logfile+" — use Copy log to paste it into a chat";
  $("go").disabled=r.running;if(!r.running&&timer){clearInterval(timer);timer=null}
  if(!r.running&&r.exit!==null&&!seen.size)$("playlists").innerHTML='<span class="hint">No playlist links this run (dry run, or nothing to build).</span>'}
$("go").onclick=async()=>{const body={links:$("links").value.split(/\n/),images,pins:$("pins").value.split(/\n/),tracks:$("tracks").value};
  for(const k of["free","dry_run","refresh","no_web","resume"])body[k]=$(k).checked;
  const r=await fetch("/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json();if(j.error){alert(j.error);return}
  $("log").textContent="";from=0;seen=new Set();$("playlists").innerHTML='<span class="hint">Working…</span>';
  if(!timer)timer=setInterval(poll,700);poll()};
$("stop").onclick=()=>fetch("/stop",{method:"POST"});
$("copy").onclick=async()=>{const t=$("log").innerText;
  try{await navigator.clipboard.writeText(t)}catch(e){const a=document.createElement("textarea");a.value=t;document.body.appendChild(a);a.select();document.execCommand("copy");a.remove()}
  $("copy").textContent="Copied!";setTimeout(()=>$("copy").textContent="Copy log",1500)};
poll();
</script></body></html>"""


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Festify UI running at {url}  (close this window to quit)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
