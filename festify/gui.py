"""festify app: paste an Instagram link, get a playlist. `python -m festify.gui` or double-click Festify.bat.

Serves one page on 127.0.0.1 only and runs festify as a subprocess, streaming its output.
Every run is saved under logs/ (Copy log puts it on the clipboard for troubleshooting). Standard library only.
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

from .config import ROOT, load_config

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
            args += ["--save-events", EVENTS_FILE]  # lets "update all saved playlists" reach this event later
    if opts.get("dry_run"):
        args.append("--dry-run")
    tracks = int(opts.get("tracks") or 3)
    if tracks != 3:
        args += ["--tracks", str(max(1, min(tracks, 10)))]
    return args


def save_uploads(images: list[dict]) -> list[str]:
    if not images:
        return []
    folder = Path(tempfile.mkdtemp(prefix="festify_upload_"))
    for i, img in enumerate(images):
        ext = Path(img.get("name", "")).suffix.lower()
        (folder / f"slide_{i:02d}{ext if ext in IMG_EXT else '.png'}").write_bytes(
            base64.b64decode(img["data"].split(",", 1)[-1]))
    return [str(folder)]  # one folder = one post, so carousel slides stay together


def start(opts: dict) -> str | None:
    with JOB.lock:
        if JOB.running:
            return "A run is already in progress."
        args = build_args(opts, save_uploads(opts.get("images", [])))
        has_pins = any(p.strip() for p in opts.get("pins", []))
        if "--save-events" not in args and not opts.get("resume") and not has_pins:
            return "Paste an Instagram link (or add screenshots) first."
        JOB.lines, JOB.exit = [f"$ festify {' '.join(args)}"], None
        (ROOT / "logs").mkdir(exist_ok=True)
        JOB.logfile = ROOT / "logs" / f"festify_{time.strftime('%Y%m%d_%H%M%S')}.log"
        JOB.logfile.write_text(JOB.lines[0] + "\n", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        cities = (opts.get("cities") or "").strip()
        if cities:
            env["LOCAL_CITIES"] = cities
        JOB.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "festify", *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    threading.Thread(target=_pump, args=(JOB.proc,), daemon=True).start()
    return None


def _pump(proc: subprocess.Popen):
    with open(JOB.logfile, "a", encoding="utf-8") as f:
        for line in proc.stdout:
            JOB.lines.append(line.rstrip("\n"))
            f.write(line)
            f.flush()
        JOB.exit = proc.wait()
        JOB.lines.append(f"[finished with exit code {JOB.exit}]")
        f.write(JOB.lines[-1] + "\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
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
            return self._send(200, PAGE.replace("__CITIES__", "; ".join(load_config().local_cities)), "text/html")
        m = re.match(r"/log\?from=(\d+)", self.path)
        if m:
            i = int(m.group(1))
            return self._send(200, {"lines": JOB.lines[i:], "next": len(JOB.lines), "running": JOB.running,
                                    "exit": JOB.exit, "logfile": str(JOB.logfile or "")})
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
.wrap{max-width:760px;margin:auto;padding:28px 18px 40px}
h1{margin:0;font-size:28px;letter-spacing:-.02em}h1 span{color:var(--accent)}.sub{margin:2px 0 18px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.row{display:flex;gap:10px}
input[type=text],textarea,input[type=number]{width:100%;border:1px solid var(--line);border-radius:10px;padding:12px;background:var(--bg);color:var(--ink);font:inherit}
textarea{min-height:70px;resize:vertical}
button{border:0;border-radius:999px;padding:12px 22px;font:600 15px system-ui;cursor:pointer;white-space:nowrap}
#go{background:var(--accent);color:var(--accent-ink)}#go:disabled{opacity:.5;cursor:default}
.ghost{background:transparent;border:1px solid var(--line);color:var(--ink)}.small{padding:6px 14px;font-size:13px}
.hint{color:var(--muted);font-size:13px}.drop{margin-top:10px;border:1.5px dashed var(--line);border-radius:10px;padding:10px;text-align:center;color:var(--muted);cursor:pointer;font-size:13px}
.drop.on{border-color:var(--accent);color:var(--ink)}
details{margin-top:12px}summary{cursor:pointer;color:var(--muted)}details .grid{display:grid;gap:10px;margin-top:10px}
label.h{font-weight:600;font-size:13px}
#status{display:flex;align-items:center;gap:8px;font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;background:var(--muted)}.dot.run{background:var(--accent);animation:p 1s infinite}@keyframes p{50%{opacity:.3}}
#step{color:var(--muted);font-weight:400;font-size:13px}
.result a.pl{display:block;font-size:17px;font-weight:600;color:var(--ink);text-decoration:none;padding:12px 14px;border:1px solid var(--line);border-radius:10px;margin:10px 0}
.result a.pl:hover{border-color:var(--accent)}.result a.pl small{display:block;color:var(--muted);font-weight:400;font-size:13px}
.warn{color:var(--warn);font-size:14px;margin:6px 0 0}
pre{background:var(--log);color:var(--logink);border-radius:12px;padding:14px;max-height:50vh;overflow:auto;margin:10px 0 0;font:12.5px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap}
pre a{color:#4ade80}.stop{color:#fbbf24;font-weight:700}
</style></head><body><div class="wrap">
<h1>Fest<span>ify</span></h1><p class="sub">Paste an Instagram concert post. Get a Spotify playlist of the bands on it.</p>
<div class="card">
  <div class="row"><input type="text" id="link" placeholder="https://www.instagram.com/p/..." autofocus><button id="go">Build playlist</button></div>
  <div class="drop" id="drop">or drop screenshots of the post here</div><input type="file" id="files" accept="image/*" multiple hidden>
  <div id="filelist" class="hint"></div>
  <details><summary>More options</summary><div class="grid">
    <div><label class="h">Local cities</label><input type="text" id="cities" value="__CITIES__"><div class="hint">Bands from near these cities are preferred. Separate with ;</div></div>
    <div class="row"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="dry_run"> Preview only (don't change Spotify)</label>
      <label style="display:flex;gap:8px;align-items:center;margin-left:auto">Songs per band <input type="number" id="tracks" value="3" min="1" max="10" style="width:64px"></label></div>
    <div><label class="h">Fix a band</label><textarea id="pins" placeholder="Band Name=https://open.spotify.com/artist/...&#10;one per line"></textarea>
      <div class="hint">Locks a band to the right Spotify artist. Then click "Update all saved playlists".</div></div>
    <div><button class="ghost small" id="resume">Update all saved playlists</button> <span class="hint">re-checks every post you've built before</span></div>
  </div></details>
</div>
<div class="card" id="out" hidden>
  <div id="status"><span class="dot" id="dot"></span><span id="st">Working</span><span id="step"></span>
    <button class="ghost small" id="stop" style="margin-left:auto">Stop</button><button class="ghost small" id="copy">Copy log</button></div>
  <div class="result" id="result"></div>
  <details id="logbox"><summary>Log</summary><pre id="log"></pre><div class="hint" id="logpath"></div></details>
</div>
</div>
<script>
const $=id=>document.getElementById(id);let images=[],from=0,timer=null,ev=null,cards=[];
const drop=$("drop"),files=$("files");drop.onclick=()=>files.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add("on")};drop.ondragleave=()=>drop.classList.remove("on");
drop.ondrop=e=>{e.preventDefault();drop.classList.remove("on");add(e.dataTransfer.files)};files.onchange=()=>add(files.files);
function add(list){[...list].forEach(f=>{const r=new FileReader();r.onload=()=>{images.push({name:f.name,data:r.result});$("filelist").textContent=images.length+" screenshot(s) added (reload to clear)"};r.readAsDataURL(f)})}
const esc=s=>s.replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const linkify=s=>esc(s).replace(/(https:\/\/open\.spotify\.com\/[^\s)]+)/g,'<a href="$1" target="_blank" rel="noopener">$1</a>');
function render(){$("result").innerHTML=cards.map(c=>`<a class="pl" href="${c.url||'#'}" target="_blank" rel="noopener">${esc(c.title)}<small>${esc(c.note)}</small></a>`+(c.desc&&c.desc.indexOf("all ")<0?`<div class="warn">${esc(c.desc.replace(/^festify:\s*/,"Check: "))}</div>`:""+(c.desc?`<div class="hint">${esc(c.desc.replace(/^festify:\s*/,""))}</div>`:""))).join("")}
function addLines(lines){const log=$("log");
  for(const l of lines){
    let m;
    if(m=l.match(/^Found (\d+) event/))$("step").textContent="· read the poster";
    if(m=l.match(/^Resolving (\d+) artists for (.+)\.\.\.$/))$("step").textContent=`· finding ${m[1]} bands for ${m[2]}`;
    if(!l.startsWith(" ")&&/ – |\s{3}\[/.test(l)&&!l.startsWith("=")&&!l.startsWith("$"))ev={title:l.split("   [")[0],note:"",url:"",desc:""};
    if(m=l.match(/^Playlist: (https:\/\/open\.spotify\.com\/playlist\/\w+)(.*)$/)){if(ev){ev.url=m[1];ev.note=m[2].trim()||"new playlist";cards.push(ev)}}
    else if(l.startsWith("Playlist: ")&&ev){ev.note=l.slice(10);cards.push(ev)}
    if(m=l.match(/^Description: (.*)$/)){if(cards.length)cards[cards.length-1].desc=m[1];render()}
    if(l.startsWith("STOPPED")){$("result").insertAdjacentHTML("beforeend",`<div class="warn">${esc(l)}</div>`)}
    log.insertAdjacentHTML("beforeend",(l.startsWith("STOPPED")?'<span class="stop">'+linkify(l)+"</span>":linkify(l))+"\n")}
  log.scrollTop=log.scrollHeight}
async function poll(){const r=await (await fetch("/log?from="+from)).json();from=r.next;addLines(r.lines);
  if(r.logfile)$("logpath").textContent="Saved to "+r.logfile;
  $("dot").className="dot"+(r.running?" run":"");$("st").textContent=r.running?"Working":(r.exit===0?"Done":"Stopped");
  if(!r.running&&r.exit!==null){$("step").textContent="";if(!cards.length&&!$("result").innerHTML)$("result").innerHTML='<div class="hint">No playlist this time — open the log for details.</div>'}
  $("go").disabled=$("resume").disabled=r.running;if(!r.running&&timer){clearInterval(timer);timer=null}}
async function run(extra){const body={links:[$("link").value],images,pins:$("pins").value.split(/\n/),tracks:$("tracks").value,
    cities:$("cities").value,dry_run:$("dry_run").checked,...extra};
  const r=await fetch("/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json();if(j.error){alert(j.error);return}
  $("out").hidden=false;$("log").textContent="";$("result").innerHTML="";from=0;cards=[];ev=null;$("step").textContent="· starting";
  if(!timer)timer=setInterval(poll,700);poll()}
$("go").onclick=()=>run({});$("link").onkeydown=e=>{if(e.key==="Enter")run({})};
$("resume").onclick=()=>run({resume:true});
$("stop").onclick=()=>fetch("/stop",{method:"POST"});
$("copy").onclick=async()=>{const t=$("log").innerText;try{await navigator.clipboard.writeText(t)}catch(e){const a=document.createElement("textarea");a.value=t;document.body.appendChild(a);a.select();document.execCommand("copy");a.remove()}
  $("copy").textContent="Copied!";setTimeout(()=>$("copy").textContent="Copy log",1500)};
</script></body></html>"""


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Festify is running at {url}  (close this window to quit)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
