"""A small MAX+PLUS II-style application: a local web server on 127.0.0.1 and a page in the
browser. Standard library only. `python -m apu gui [project directory]`.

Panels: the project's design files; a source viewer; Compile (parse, synthesise, fit, pins);
Simulate (functional, from a .tbl's inputs or from a clock count, drawn as a waveform, with the
vendor's expected values compared when a .tbl is used); Programmer (APU adapter ID and silicon ID;
EPM7064S on a USB-Blaster: ID and full read-back to a POF), all read-only. Compile writes
programming files (POF, JEDEC, and an SVF for the EPM7064S) for the EPM7032 and EPM7064S.
Nothing in this app writes to a chip: program with `apu jtag program file.pof --yes`.
"""
import html
import json
import pathlib
import re
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DESIGN_SUFFIXES = (".tdf", ".gdf")


class Project:
    def __init__(self, directory, vendor):
        self.dir = pathlib.Path(directory).resolve()
        self.vendor = pathlib.Path(vendor)
        self.lock = threading.Lock()                     # one hardware operation at a time

    # -- helpers ------------------------------------------------------------------------------
    def path(self, name):
        p = (self.dir / name).resolve()
        if p.parent != self.dir or not p.exists():
            raise ValueError("no such file in the project: %s" % name)
        return p

    def resolver(self):
        from . import ahdl_eval
        return ahdl_eval.directory_resolver(self.dir, *ahdl_eval.vendor_library(self.vendor))

    def design_text(self, name):
        from . import gdf
        p = self.path(name)
        if p.suffix.lower() == ".gdf":
            return gdf.to_ahdl(p.read_bytes(), p.stem)
        return p.read_text(encoding="latin1")

    # -- actions ------------------------------------------------------------------------------
    def files(self):
        out = []
        for p in sorted(self.dir.iterdir()):
            if p.is_file() and p.suffix.lower() in DESIGN_SUFFIXES + (".tbl", ".inc", ".jed", ".pof"):
                out.append({"name": p.name, "kind": p.suffix.lower()[1:], "size": p.stat().st_size})
        return {"directory": str(self.dir), "files": out}

    def source(self, name):
        p = self.path(name)
        if p.suffix.lower() == ".gdf":
            return {"name": name, "text": self.design_text(name),
                    "note": "schematic, shown as the AHDL it converts to"}
        if p.suffix.lower() in (".jed", ".tdf", ".inc", ".tbl"):
            return {"name": name, "text": p.read_text(encoding="latin1")}
        if p.suffix.lower() == ".pof":
            from . import pof
            f = pof.Pof.from_bytes(p.read_bytes())
            lines = ["device %r, creator %r, CRC %s" % (f.device, f.creator, "ok" if f.crc_ok else "BAD")]
            lines += ["electrical block: lines %d..%d, cells %d..%d" % (e.start_col, e.start_col + e.col_count - 1,
                                                                   e.start_row, e.start_row + e.row_count - 1)
                      for e in f.electrical()]
            return {"name": name, "text": "\n".join(lines)}
        raise ValueError("cannot show %s" % name)

    def compile(self, name, device):
        from . import acf, daf, fit, fusemap, jedec, maf, parts, pof, project, synth
        from .__main__ import FIT_DEVICES
        device = device.upper()
        if device not in FIT_DEVICES:
            raise ValueError("unknown device %s" % device)
        k = self.vendor.parent / "7k"
        net = synth.synthesise(self.design_text(name), self.resolver())
        design = self.path(name)
        acf_path = design.with_suffix(".acf")
        fixed = acf.pins(acf.parse(acf_path.read_text(encoding="latin1")), design.stem) if acf_path.exists() else {}
        part_name = {"EPM7032": "EPM7032LC44", "EPM7064S": "EPM7064SLC44"}.get(device)
        if part_name is None:                            # resource fit only for the others
            device_file = daf.parse((k / FIT_DEVICES[device]).read_bytes())
            result = fit.fit(net, device, daf.summary(device_file)["macrocells"])
            try:
                _, pins = fit.assign_pins(result, net, device_file.tables["pin_mc"])
            except fit.DoesNotFit as e:
                pins = {"(failed)": str(e)}
            return {"equations": net.report(), "fit": result.report(),
                    "pins": sorted(pins.items(), key=lambda kv: (isinstance(kv[1], str), kv[1])),
                    "note": "Resource fit only for %s: no programming file is produced (EPM7032 and EPM7064S only)." % device}
        part = parts.lookup(parts.load(self.vendor / "partinfo.crf"), part_name)
        written = []
        if device == "EPM7064S":
            from . import generate7064s, svf7064s
            fm = fusemap.FuseMap(daf.parse((k / "s7064044.daf").read_bytes()), maf.parse((k / "m7064s.maf").read_bytes()))
            fuses, fitted = generate7064s.compile_netlist(net, part, k, fixed, fm)
            design.with_suffix(".svf").write_text(svf7064s.write(fuses, "program"))
            written.append(design.with_suffix(".svf").name)
        else:
            from . import generate
            fitted = project.fit_part(net, part, k, fixed)
            fm = fusemap.FuseMap(daf.parse((k / "m7032044.daf").read_bytes()), maf.parse((k / "m7032.maf").read_bytes()))
            fuses = generate.generate(net, fitted.result, fitted.placement, fitted.pins, fm)
        design.with_suffix(".pof").write_bytes(pof.fuses_to_logical_pof(fuses, part_name, "apu-revive").to_bytes())
        design.with_suffix(".jed").write_bytes(jedec.write(fuses, "Compiled by apu-revive from %s" % design.name,
                                                           device=part_name).encode("latin1"))
        written = [design.with_suffix(".pof").name, design.with_suffix(".jed").name] + written
        return {"equations": net.report(), "fit": fitted.result.report(),
                "pins": sorted(fitted.pins.items(), key=lambda kv: kv[1]),
                "note": "Programming files written: %s." % ", ".join(written)}

    def simulate(self, name, tbl_name=None, clocks=16, clock="clk"):
        from . import ahdl_eval, tbl
        sim = ahdl_eval.Simulator(self.design_text(name), self.resolver())
        d = sim.design
        signals = list(d.inputs) + list(d.outputs)
        trace = []

        def sample(label):
            values = dict(sim.inputs)
            values.update(sim.outputs())
            trace.append((label, {s: values.get(s, values.get(s + "@ext", 0)) for s in signals}))
        mismatches, checks = [], None
        if tbl_name:
            table = tbl.parse(self.path(tbl_name).read_text(encoding="latin1"))
            rename = lambda n: re.sub(r"\|[\w~]+:(\d+)\|", r"u\1/", n).lstrip("|")
            checks, mismatches = tbl.compare(table, sim, rename, table.outputs + table.buried)
            sim = ahdl_eval.Simulator(self.design_text(name), self.resolver())   # replay for the drawing
            last = None
            for time, row in table.rows:
                ins = {n: row[n] for n in table.inputs}
                if any("X" in v.upper() for v in ins.values()):
                    break
                if ins != last:
                    sim.set(**{rename(n): int(v, table.radix) for n, v in ins.items()})
                    last = ins
                    sample("%g ns" % time)
        else:
            if clock not in d.inputs:
                clock = next((i for i in d.inputs if "clk" in i or "clock" in i), None)
            sample("start")
            for k in range(int(clocks)):
                if clock:
                    sim.set(**{clock: 1}); sample("%d up" % (k + 1))
                    sim.set(**{clock: 0}); sample("%d down" % (k + 1))
        return {"signals": signals, "trace": [[label, [vals[s] for s in signals]] for label, vals in trace],
                "checks": checks, "mismatches": [list(m) for m in mismatches[:50]]}

    def jtag(self, action, name=None):
        """EPM7064S on a USB-Blaster, read-only: its ID, or a full read-back to <name>.pof."""
        from . import jtag, pof
        with self.lock, jtag.Openocd() as ocd:
            code = jtag.idcode(ocd)
            out = {"idcode": "%08X" % code, "device": "EPM7064S" if code == jtag.IDCODE_EPM7064S else "unknown"}
            if action == "read" and code == jtag.IDCODE_EPM7064S:
                fuses = jtag.read_fuses(ocd)
                target = self.dir / ((pathlib.Path(name).stem if name else "chip") + "_readback.pof")
                target.write_bytes(pof.fuses_to_logical_pof(fuses, "EPM7064SLC44", "apu-revive").to_bytes())
                out.update(fuses=len(fuses), programmed=fuses.count(0), written=target.name)
            return out

    def programmer(self):
        from . import board, programmer
        from .device import Apu
        with self.lock:
            apu = Apu.open()
            try:
                apu.load_firmware((self.vendor / "pgdmpuz.drv").read_bytes())
                apu.configure_fpga((self.vendor / "pgdmpuz.rbf").read_bytes())
                programmer.initialise(apu)
                aid = board.adapter_id(apu)
                result = {"adapter": aid, "adapter_name": board.KNOWN_ADAPTERS.get(aid, "unknown"),
                          "supply": apu.has_9v()}
                profile = next((p for p in programmer.PROFILES.values() if aid in p.adapters and p.verified), None)
                if profile and result["supply"]:
                    from .pcf import Pcf
                    from .__main__ import DEVICE_FILES
                    pcf = Pcf.load(self.vendor.parent / "7k" / DEVICE_FILES[profile.name])
                    sig = programmer.silicon_id(apu, profile, pcf)
                    result["device"] = profile.name
                    result["silicon_id"] = sig[:8].decode("latin1")
                return result
            finally:
                try:
                    apu.dev.reset()
                except Exception:
                    pass


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>apu-revive</title>
<style>
 body{margin:0;font:13px -apple-system,Helvetica,sans-serif;display:flex;height:100vh;color:#222}
 #side{width:230px;border-right:1px solid #ccc;overflow:auto;background:#f6f6f6}
 #side h3{margin:10px;font-size:12px;text-transform:uppercase;color:#666}
 .file{padding:3px 12px;cursor:pointer}.file:hover{background:#e4e4e4}.file.sel{background:#cfe0ff}
 .kind{color:#888;font-size:11px;margin-left:4px}
 #main{flex:1;display:flex;flex-direction:column;min-width:0}
 #bar{padding:8px;border-bottom:1px solid #ccc;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
 button{font:inherit;padding:3px 10px}
 #out{flex:1;overflow:auto;padding:10px}
 pre{font:12px Menlo,monospace;white-space:pre-wrap;margin:0}
 .err{color:#b00}.ok{color:#070}
 table.wave{border-collapse:collapse;font:11px Menlo,monospace}
 table.wave td{padding:0;height:18px}table.wave td.n{padding-right:8px;text-align:right;white-space:nowrap}
 .h{background:#3a7}.l{background:#fff;border-bottom:1px solid #3a7}
</style></head><body>
<div id="side"><h3>Project</h3><div id="dir" style="margin:0 12px 8px;font-size:11px;color:#888"></div><div id="files"></div></div>
<div id="main">
 <div id="bar">
  <b id="cur">no file selected</b>
  <button onclick="show()">Source</button>
  <select id="dev"><option>EPM7032</option><option>EPM7064</option><option>EPM7096</option><option>EPM7128</option><option>EPM7160E</option><option>EPM7064S</option></select>
  <button onclick="comp()">Compile</button>
  <select id="tbl"><option value="">clock only</option></select>
  <button onclick="simu()">Simulate</button>
  <span style="flex:1"></span>
  <button onclick="prog()">APU: read ID</button>
  <button onclick="jt('id')">USB-Blaster: ID</button>
  <button onclick="jt('read')">USB-Blaster: read chip</button>
 </div>
 <div id="out"><p>Select a design file on the left.</p></div>
</div>
<script>
let cur=null;
const out=document.getElementById('out');
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function api(path,params){
  const q=new URLSearchParams(params||{});
  const r=await fetch('/api/'+path+'?'+q); const j=await r.json();
  if(j.error) throw new Error(j.error); return j;
}
function busy(t){out.innerHTML='<p>'+esc(t)+'</p>'}
function fail(e){out.innerHTML='<pre class="err">'+esc(e.message)+'</pre>'}
async function load(){
  const j=await api('files'); document.getElementById('dir').textContent=j.directory;
  const f=document.getElementById('files'), t=document.getElementById('tbl'); f.innerHTML='';
  for(const x of j.files){
    const d=document.createElement('div'); d.className='file';
    d.innerHTML=esc(x.name)+'<span class="kind">'+x.kind+'</span>';
    d.onclick=()=>{cur=x.name;document.querySelectorAll('.file').forEach(e=>e.classList.remove('sel'));d.classList.add('sel');
      document.getElementById('cur').textContent=x.name;show()};
    f.appendChild(d);
    if(x.kind=='tbl'){const o=document.createElement('option');o.value=x.name;o.textContent='vectors: '+x.name;t.appendChild(o)}
  }
}
async function show(){ if(!cur) return; busy('Loading…');
  try{const j=await api('source',{name:cur}); out.innerHTML=(j.note?'<p><i>'+esc(j.note)+'</i></p>':'')+'<pre>'+esc(j.text)+'</pre>'}catch(e){fail(e)} }
async function comp(){ if(!cur) return; busy('Compiling…');
  try{const j=await api('compile',{name:cur,device:document.getElementById('dev').value});
    out.innerHTML='<h4>Fit</h4><pre>'+esc(j.fit)+'</pre><h4>Pins</h4><pre>'+esc(j.pins.map(p=>p[0]+' = '+p[1]).join('\\n'))+
      '</pre><h4>Equations</h4><pre>'+esc(j.equations)+'</pre><p><i>'+esc(j.note)+'</i></p>'}catch(e){fail(e)} }
async function simu(){ if(!cur) return; busy('Simulating…');
  try{const tb=document.getElementById('tbl').value; const j=await api('simulate',tb?{name:cur,tbl:tb}:{name:cur,clocks:16});
    let h='';
    if(j.checks!==null){h+='<p class="'+(j.mismatches.length?'err':'ok')+'">Compared with the vendor simulator: '+j.checks+' values, '+j.mismatches.length+' differ.</p>';
      for(const m of j.mismatches) h+='<pre class="err">'+esc(m[0]+' ns  '+m[1]+': expected '+m[2]+', got '+m[3])+'</pre>'}
    h+='<table class="wave">';
    j.signals.forEach((s,i)=>{h+='<tr><td class="n">'+esc(s)+'</td>'; for(const row of j.trace) h+='<td title="'+esc(row[0])+'" class="'+(row[1][i]?'h':'l')+'" style="width:10px"></td>'; h+='</tr>'});
    out.innerHTML=h+'</table><p><i>Functional simulation (no delays). Each column is one step.</i></p>'}catch(e){fail(e)} }
async function prog(){ busy('Talking to the programmer…');
  try{const j=await api('programmer'); out.innerHTML='<pre>'+esc(JSON.stringify(j,null,2))+'</pre><p><i>Read-only: this app never writes to a chip.</i></p>'}catch(e){fail(e)} }
async function jt(a){ busy('Talking to the USB-Blaster…');
  try{const j=await api('jtag',{action:a,name:cur||''}); out.innerHTML='<pre>'+esc(JSON.stringify(j,null,2))+'</pre><p><i>Read-only. To program: apu jtag program file.pof --yes</i></p>'}catch(e){fail(e)} }
load();
</script></body></html>
"""


def make_handler(project):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, kind="application/json"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self.send(200, PAGE, "text/html; charset=utf-8")
            if not url.path.startswith("/api/"):
                return self.send(404, json.dumps({"error": "not found"}))
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            action = url.path[5:]
            try:
                if action == "files":
                    result = project.files()
                elif action == "source":
                    result = project.source(q["name"])
                elif action == "compile":
                    result = project.compile(q["name"], q.get("device", "EPM7032"))
                elif action == "simulate":
                    result = project.simulate(q["name"], q.get("tbl") or None, int(q.get("clocks", 16)))
                elif action == "programmer":
                    result = project.programmer()
                elif action == "jtag":
                    if q.get("action") not in ("id", "read"):
                        raise ValueError("the app only reads chips")
                    result = project.jtag(q["action"], q.get("name") or None)
                else:
                    return self.send(404, json.dumps({"error": "unknown action"}))
                self.send(200, json.dumps(result))
            except Exception as e:                       # report, keep serving
                self.send(200, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}))
    return Handler


def serve(directory, vendor, port=0, open_browser=True):
    """Start the app; returns the server (call serve_forever, or shutdown when embedded)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Project(directory, vendor)))
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    if open_browser:
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    server.url = url
    return server
