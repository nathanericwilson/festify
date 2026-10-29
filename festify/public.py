"""festify's public page: anyone pastes an Instagram post link and gets a Spotify playlist link.

`python -m festify.public` serves it on 127.0.0.1:8770. Public.bat also starts a Cloudflare Tunnel so
other people can reach it. Playlists are made PUBLIC on the owner's Spotify account, named "festify · …".

It's built to be safe to leave open:
  * only Instagram post/reel links are accepted (no uploads, no options);
  * a post that was already made returns its playlist instantly (no API calls);
  * per-visitor and daily caps, a short queue, and one job at a time;
  * a Spotify rate-limit ban or a free-tier quota pauses new jobs instead of failing repeatedly;
  * optional Cloudflare Turnstile bot check; visitor IPs are only counted in memory, never written to disk;
  * it can be embedded (?embed=1) only on sites listed in PUBLIC_EMBED_ORIGINS.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import traceback
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import requests

from .config import ROOT, load_config
from .instagram import IG_RE

HOST, PORT = "127.0.0.1", int(os.environ.get("PUBLIC_PORT", "8770"))
TURNSTILE_VERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def shortcode(url: str) -> str | None:
    """The post's shortcode if this is an Instagram post/reel link, else None."""
    url = (url or "").strip()
    if len(url) > 300 or not re.match(r"^(https?://)?([\w-]+\.)?instagram\.com/", url, re.I):
        return None
    m = IG_RE.search(url)
    return m.group(1) if m and 5 <= len(m.group(1)) <= 40 else None


class Gate:
    """Daily caps, queue size and pauses. Counts reset each day and live only in memory."""

    def __init__(self, daily: int = 20, per_visitor: int = 3, max_queue: int = 5, clock=time.time):
        self.daily, self.per_visitor, self.max_queue, self.clock = daily, per_visitor, max_queue, clock
        self.day, self.total, self.visitors = date.today(), 0, {}
        self.paused_until, self.pause_reason = 0.0, ""
        self.lock = threading.Lock()

    def _roll(self):
        if date.today() != self.day:
            self.day, self.total, self.visitors = date.today(), 0, {}

    def check(self, visitor: str, queued: int) -> str | None:
        """None if a new job may start, else a friendly reason."""
        with self.lock:
            self._roll()
            if self.clock() < self.paused_until:
                mins = int((self.paused_until - self.clock()) / 60) + 1
                when = f"{mins} minutes" if mins < 120 else f"{mins // 60} hours"
                return f"festify is taking a break ({self.pause_reason}). Try again in about {when}."
            if self.total >= self.daily:
                return "festify has made all the playlists it can today. Try again tomorrow."
            if self.visitors.get(visitor, 0) >= self.per_visitor:
                return f"You've made {self.per_visitor} playlists today, the daily limit. Try again tomorrow."
            if queued >= self.max_queue:
                return "festify is busy right now. Try again in a few minutes."
            return None

    def take(self, visitor: str):
        with self.lock:
            self._roll()
            self.total += 1
            self.visitors[visitor] = self.visitors.get(visitor, 0) + 1

    def refund(self, visitor: str):
        with self.lock:
            self.total = max(0, self.total - 1)
            if self.visitors.get(visitor):
                self.visitors[visitor] -= 1

    def pause(self, seconds: float, reason: str):
        with self.lock:
            self.paused_until = max(self.paused_until, self.clock() + seconds)
            self.pause_reason = reason


class Service:
    """Jobs, the queue and the worker. `builder(url, progress)` makes the playlists (injectable for tests)."""

    def __init__(self, cache, builder, gate: Gate, log=print, turnstile_secret: str = ""):
        self.cache, self.builder, self.gate, self.log = cache, builder, gate, log
        self.turnstile_secret = turnstile_secret
        self.jobs: dict[str, dict] = {}
        self.q: queue.Queue = queue.Queue()
        self.by_code: dict[str, str] = {}  # shortcode -> job id while it's queued/running

    # ---- requests -----------------------------------------------------------
    def make(self, url: str, visitor: str, token: str = "") -> tuple[int, dict]:
        code = shortcode(url)
        if not code:
            return 400, {"error": "Paste a link to an Instagram post, like https://www.instagram.com/p/ABC123/"}
        done = self.cache.get("public_posts", code)
        if done and done.get("playlists"):
            return 200, {"done": True, "playlists": done["playlists"]}
        if code in self.by_code:  # someone already asked for this post: follow that job
            return 200, {"job": self.by_code[code]}
        if self.turnstile_secret and not self._human(token, visitor):
            return 403, {"error": "Please complete the check and try again."}
        reason = self.gate.check(visitor, self.q.qsize())
        if reason:
            return 429, {"error": reason}
        self.gate.take(visitor)
        jid = uuid.uuid4().hex[:12]
        self.jobs[jid] = {"code": code, "status": "queued", "step": "Waiting in line…", "visitor": visitor,
                          "started": time.time(), "playlists": [], "error": ""}
        self.by_code[code] = jid
        self.q.put(jid)
        return 200, {"job": jid}

    def status(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        if not j:
            return None
        return {"status": j["status"], "step": j["step"], "playlists": j["playlists"], "error": j["error"]}

    def recent(self, n: int = 20) -> list[dict]:
        posts = (self.cache.data.get("public_posts") or {}).values()
        items = [dict(p, made=v.get("made", "")) for v in posts for p in v.get("playlists", [])]
        return sorted(items, key=lambda p: p["made"], reverse=True)[:n]

    def _human(self, token: str, visitor: str) -> bool:
        try:
            r = requests.post(TURNSTILE_VERIFY, data={"secret": self.turnstile_secret, "response": token,
                                                      "remoteip": visitor}, timeout=10)
            return bool(r.json().get("success"))
        except Exception:
            return False

    # ---- worker -------------------------------------------------------------
    def work_one(self, jid: str):
        from .cli import TooBig
        from .instagram import IGFetchError
        from .llm import BillingError

        j = self.jobs[jid]
        j["status"], j["step"] = "running", "Starting…"
        url = f"https://www.instagram.com/p/{j['code']}/"
        self.log(f"job {jid} start {url}")

        def progress(msg):
            j["step"] = msg

        try:
            made = self.builder(url, progress)
            if made:
                j["playlists"], j["status"], j["step"] = made, "done", "Done"
                self.cache.set("public_posts", j["code"], {"playlists": made, "made": datetime.now().isoformat(timespec="seconds")})
            else:
                j["status"], j["error"] = "failed", "Couldn't find songs on Spotify for the bands in that post."
        except TooBig as e:
            j["status"], j["error"] = "failed", str(e)
        except IGFetchError:
            j["status"], j["error"] = "failed", ("Couldn't open that Instagram post. It may be private, or Instagram "
                                                 "is limiting requests. Try again later.")
        except BillingError as e:
            wait = getattr(e, "retry_after", 0) or 3600
            reason = "Spotify's limit" if "Spotify" in str(e) else "a free-tier limit"
            self.gate.pause(wait, reason)
            j["status"], j["error"] = "failed", f"festify hit {reason} and is taking a break. Try again later."
        except Exception:
            j["status"], j["error"] = "failed", "Something went wrong. Try again later."
            self.log(traceback.format_exc())
        finally:
            if j["status"] != "done":
                self.gate.refund(j["visitor"])
            self.by_code.pop(j["code"], None)
            j.pop("visitor", None)  # don't keep visitor addresses around
            self.log(f"job {jid} {j['status']} {j['error']}")

    def worker(self):
        while True:
            jid = self.q.get()
            try:
                self.work_one(jid)
            finally:
                self.q.task_done()


# ---------------------------------------------------------------------------- web
def make_handler(svc: Service, site_key: str = "", embed_origins: str = ""):
    frame = "frame-ancestors " + (embed_origins.strip() or "'none'")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if "image" not in ctype else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", frame)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _visitor(self) -> str:
            return self.headers.get("CF-Connecting-IP") or self.client_address[0]

        def do_GET(self):
            path, _, qs = self.path.partition("?")
            if path == "/":
                embed = "embed=1" in qs
                return self._send(200, page(site_key, embed), "text/html")
            if path == "/favicon.png":
                return self._send(200, (ROOT / "assets" / "festify.png").read_bytes(), "image/png")
            if path == "/recent":
                return self._send(200, {"playlists": svc.recent()})
            m = re.fullmatch(r"/job/([0-9a-f]{12})", path)
            if m:
                st = svc.status(m.group(1))
                return self._send(200 if st else 404, st or {"error": "not found"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/make":
                return self._send(404, {"error": "not found"})
            origin, host = self.headers.get("Origin", ""), self.headers.get("Host", "")
            if origin and re.sub(r"^https?://", "", origin) != host:
                return self._send(403, {"error": "forbidden"})
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n > 4096:
                return self._send(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad request"})
            code, out = svc.make(str(body.get("url", "")), self._visitor(), str(body.get("token", "")))
            self._send(code, out)

    return Handler


def page(site_key: str, embed: bool) -> str:
    ts = (f'<div class="cf-turnstile" data-sitekey="{site_key}" data-theme="dark"></div>'
          '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>') if site_key else ""
    return (PAGE.replace("__TURNSTILE__", ts).replace("__EMBED__", "embed" if embed else ""))


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FESTIFY.EXE</title><link rel="icon" href="/favicon.png">
<meta name="description" content="Paste an Instagram concert post, get a Spotify playlist of the bands on it.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=VT323&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#07080c;--screen:#0d1018;--panel:#121725;--line:#26304a;--ink:#e8e4d8;--muted:#8d93a8;
  --amber:#ffb340;--amber-dim:#a8722a;--blue:#5cc8ff;--blue-dim:#2b6f99;--warn:#ff8a5c;--glow:0 0 8px rgba(255,179,64,.45)}
*{box-sizing:border-box}
html,body{margin:0}
body{background:radial-gradient(ellipse at 50% 0%,#141a2b 0%,var(--bg) 70%) fixed;color:var(--ink);
  font:15px/1.6 "Space Mono",ui-monospace,Consolas,monospace;min-height:100vh}
body.embed{background:transparent;min-height:0}
/* CRT scanlines + vignette over everything */
body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:50;
  background:repeating-linear-gradient(to bottom,rgba(0,0,0,0) 0,rgba(0,0,0,0) 2px,rgba(0,0,0,.18) 3px,rgba(0,0,0,0) 4px),
             radial-gradient(ellipse at center,rgba(0,0,0,0) 60%,rgba(0,0,0,.45) 100%)}
.embed::after{background:repeating-linear-gradient(to bottom,rgba(0,0,0,0) 0,rgba(0,0,0,0) 2px,rgba(0,0,0,.14) 3px,rgba(0,0,0,0) 4px)}
.wrap{max-width:720px;margin:0 auto;padding:36px 16px 48px}.embed .wrap{padding:6px}
/* the program window */
.win{border:2px solid var(--line);background:var(--screen);box-shadow:0 0 0 1px #000,0 18px 60px rgba(0,0,0,.6),0 0 40px rgba(92,200,255,.06)}
.bar{display:flex;align-items:center;gap:10px;padding:6px 10px;background:linear-gradient(90deg,#1b2440,#141a2c);
  border-bottom:2px solid var(--line);font-family:VT323,monospace;font-size:22px;letter-spacing:1px;color:var(--blue)}
.bar .t{flex:1;text-shadow:0 0 6px rgba(92,200,255,.5)}
.bar .b{width:16px;height:16px;border:2px solid var(--blue-dim);display:inline-block}
.body{padding:24px}
.hero{display:flex;gap:22px;align-items:center;flex-wrap:wrap}
.logo{width:168px;height:168px;flex:none;border:2px solid var(--amber-dim);padding:6px;background:#000;
  box-shadow:0 0 0 2px #000,0 0 24px rgba(255,179,64,.25);image-rendering:auto}
.logo img{width:100%;height:100%;display:block}
h1{font-family:VT323,monospace;font-weight:400;font-size:64px;line-height:.9;margin:0;color:var(--amber);text-shadow:var(--glow);letter-spacing:2px}
h1 small{display:block;font-size:22px;color:var(--blue);letter-spacing:1px;text-shadow:0 0 6px rgba(92,200,255,.4);margin-top:6px}
.lede{margin:10px 0 0;color:var(--muted);max-width:420px}
.cursor{display:inline-block;width:.6em;height:1em;background:var(--amber);vertical-align:-2px;margin-left:4px;animation:blink 1.1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.panel{margin-top:24px;border:2px solid var(--line);background:var(--panel);padding:16px}
.label{font-family:VT323,monospace;font-size:20px;color:var(--blue);margin:0 0 8px;letter-spacing:1px}
.row{display:flex;gap:10px;flex-wrap:wrap}
.prompt{flex:1 1 300px;display:flex;align-items:center;border:2px solid var(--amber-dim);background:#05060a;padding:0 10px;min-width:0}
.prompt:focus-within{border-color:var(--amber);box-shadow:var(--glow)}
.prompt span{color:var(--amber);font-family:VT323,monospace;font-size:22px;margin-right:6px}
input{flex:1;min-width:0;border:0;outline:0;background:transparent;color:var(--ink);font:15px "Space Mono",monospace;padding:12px 0}
input::placeholder{color:#59607a}
button{border:2px solid var(--amber);background:var(--amber);color:#1a1000;font:24px VT323,monospace;letter-spacing:1px;
  padding:6px 20px;cursor:pointer;text-transform:uppercase;box-shadow:4px 4px 0 #000}
button:hover{background:#ffc566}button:active{transform:translate(2px,2px);box-shadow:2px 2px 0 #000}
button:disabled{opacity:.5;cursor:default;transform:none}
.cf-turnstile{margin-top:12px}
#status{margin-top:14px;min-height:1.6em;color:var(--blue)}#status.err{color:var(--warn)}
#status .spin::before{content:"";display:inline-block;width:10px;height:10px;background:var(--blue);margin-right:10px;animation:blink .8s steps(1) infinite}
a.pl{display:block;margin-top:14px;padding:14px 16px;border:2px solid var(--blue-dim);background:#0a1220;color:var(--ink);text-decoration:none}
a.pl:hover{border-color:var(--blue);box-shadow:0 0 14px rgba(92,200,255,.25)}
a.pl b{font-family:VT323,monospace;font-weight:400;font-size:26px;color:var(--blue);display:block;line-height:1.1}
a.pl small{display:block;color:var(--muted);font-size:13px;margin-top:6px}
.recent ol{list-style:none;margin:0;padding:0}
.recent li{border-bottom:1px dashed var(--line)}
.recent a{display:flex;gap:10px;color:var(--ink);text-decoration:none;padding:8px 0;font-size:14px}
.recent a::before{content:">";color:var(--amber)}.recent a:hover{color:var(--amber)}
.empty{color:var(--muted);font-size:14px}
footer{margin-top:18px;color:var(--muted);font-size:12px;line-height:1.6}
.embed .hero,.embed .recent,.embed footer,.embed .bar{display:none}.embed .body{padding:12px}.embed .panel{margin-top:0}
@media (max-width:520px){.hero{justify-content:center;text-align:center}.logo{width:200px;height:200px}h1{font-size:54px}.lede{margin-left:auto;margin-right:auto}.body{padding:16px}}
@media (prefers-reduced-motion:reduce){.cursor,#status .spin::before{animation:none}}
</style></head><body class="__EMBED__"><div class="wrap">
<div class="win">
  <div class="bar"><span class="t">FESTIFY.EXE</span><span class="b"></span><span class="b"></span></div>
  <div class="body">
    <div class="hero">
      <div class="logo"><img src="/favicon.png" alt="festify logo: a mosh pit and a band on stage"></div>
      <div><h1>FESTIFY<small>poster &rarr; playlist</small></h1>
        <p class="lede">Paste an Instagram concert post. Get a Spotify playlist of the bands on it, so you can tell if the show's worth going to.<span class="cursor" aria-hidden="true"></span></p></div>
    </div>
    <div class="panel">
      <p class="label">&gt; PASTE A POST LINK</p>
      <div class="row">
        <label class="prompt"><span>C:\&gt;</span><input id="url" type="url" inputmode="url" placeholder="https://www.instagram.com/p/..." aria-label="Instagram post link" autocomplete="off"></label>
        <button id="go">Run</button>
      </div>
      __TURNSTILE__
      <div id="status" role="status" aria-live="polite"></div>
      <div id="result"></div>
    </div>
    <div class="panel recent"><p class="label">&gt; RECENTLY MADE</p><ol id="recent"><li class="empty">Nothing yet.</li></ol></div>
    <footer>Playlists are created on the festify Spotify account and open in Spotify. Not affiliated with Spotify or Instagram.
    Small local bands sometimes share names with other artists; anything uncertain is noted in the playlist description.</footer>
  </div>
</div>
</div>
<script>
const $=id=>document.getElementById(id);let timer=null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const safe=u=>/^https:\/\/open\.spotify\.com\//.test(u)?u:"#";
function show(pls){$("result").innerHTML=pls.map(p=>`<a class="pl" href="${safe(p.url)}" target="_blank" rel="noopener"><b>&#9654; ${esc(p.name)}</b><small>${esc((p.description||"").replace(/\s*Made with festify\.$/,"").replace(/^festify:\s*/,""))}</small></a>`).join("")}
function status(t,err,busy){$("status").className=err?"err":"";$("status").innerHTML=(busy?'<span class="spin"></span>':"")+esc(t)}
async function poll(id){const r=await fetch("/job/"+id);if(!r.ok){status("Lost track of that request. Try again.",true);return done()}
  const j=await r.json();if(j.status==="done"){status("Done. Here's your playlist:");show(j.playlists);loadRecent();return done()}
  if(j.status==="failed"){status(j.error,true);return done()}status(j.step,false,true);timer=setTimeout(()=>poll(id),1500)}
function done(){$("go").disabled=false;if(window.turnstile)try{turnstile.reset()}catch(e){}}
async function go(){const url=$("url").value.trim();if(!url)return;$("go").disabled=true;$("result").innerHTML="";status("Starting...",false,true);
  let token="";const t=document.querySelector('[name="cf-turnstile-response"]');if(t)token=t.value;
  try{const r=await fetch("/make",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url,token})});
    const j=await r.json();if(j.error){status(j.error,true);return done()}
    if(j.done){status("Already made. Here it is:");show(j.playlists);return done()}poll(j.job)}
  catch(e){status("festify isn't reachable right now. Try again later.",true);done()}}
async function loadRecent(){try{const j=await (await fetch("/recent")).json();if(j.playlists.length)
  $("recent").innerHTML=j.playlists.map(p=>`<li><a href="${safe(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a></li>`).join("")}catch(e){}}
$("go").onclick=go;$("url").onkeydown=e=>{if(e.key==="Enter")go()};loadRecent();
</script></body></html>"""


def default_builder(log):
    from .cli import make_public_playlists

    def build(url, progress):
        return make_public_playlists(url, progress=progress, log=log,
                                     max_artists=env_int("PUBLIC_MAX_ARTISTS", 25))
    return build


def main():
    import sys

    from .cache import Cache
    from .gui import Server  # same no-port-sharing server class as the personal app

    cfg = load_config()  # also loads .env into the environment
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)

    def log(msg):
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        with open(logdir / f"public_{date.today():%Y%m%d}.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    gate = Gate(daily=env_int("PUBLIC_DAILY_LIMIT", 20), per_visitor=env_int("PUBLIC_PER_VISITOR", 3))
    svc = Service(Cache(cfg.cache_path), default_builder(log), gate, log=log,
                  turnstile_secret=os.environ.get("TURNSTILE_SECRET", "").strip())
    threading.Thread(target=svc.worker, daemon=True).start()
    handler = make_handler(svc, os.environ.get("TURNSTILE_SITE_KEY", "").strip(),
                           os.environ.get("PUBLIC_EMBED_ORIGINS", "").strip())
    try:
        srv = Server((HOST, PORT), handler)
    except OSError:
        sys.exit(f"Port {PORT} is in use. Is festify's public page already running? Close it and try again.")
    log(f"festify public page running at http://{HOST}:{PORT}/ (limits: {gate.daily}/day, {gate.per_visitor} per visitor)")
    tunnel = start_tunnel(log) if "--tunnel" in sys.argv else None
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.terminate()


def cloudflared_path() -> str | None:
    import shutil

    for p in (shutil.which("cloudflared"), r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
              r"C:\Program Files\cloudflared\cloudflared.exe"):
        if p and Path(p).exists():
            return p
    return None


def start_tunnel(log):
    """A free Cloudflare quick tunnel: a public https address (it changes each time) that forwards here.
    For a fixed address on your own domain, see README "Share it with others"."""
    import subprocess
    import sys
    import webbrowser

    exe = cloudflared_path()
    if not exe:
        log("! cloudflared isn't installed (winget install Cloudflare.cloudflared); the page is only on this PC.")
        return None
    name = os.environ.get("PUBLIC_TUNNEL_NAME", "").strip()  # a named tunnel on your own domain, once set up
    cmd = [exe, "tunnel", "run", name] if name else [exe, "tunnel", "--no-autoupdate", "--url", f"http://{HOST}:{PORT}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def watch():
        for line in proc.stdout:
            m = re.search(r"https://[\w-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                (ROOT / "logs" / "public_url.txt").write_text(url + "\n", encoding="utf-8")
                log(f"PUBLIC ADDRESS: {url}  (share this; it changes each time festify public restarts)")
                if "--no-browser" not in sys.argv:
                    webbrowser.open(url)
            elif "ERR" in line:
                log("tunnel: " + line.strip()[:200])
    threading.Thread(target=watch, daemon=True).start()
    log("starting the Cloudflare tunnel…" + (f" (named tunnel '{name}')" if name else ""))
    return proc


if __name__ == "__main__":
    main()
