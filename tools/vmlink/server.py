"""Host side of a small command link to a Windows 2000 guest with no guest agent.

The guest runs agent.vbs, which polls this server for work. Work is queued as files:
  <dir>/queue/<n>.cmd      one line: RUN <cmd.exe command line> | PUT <outbox name> <guest path>
                           | SEND <guest path> | QUIT
  <dir>/results/<n>.txt    what came back (exit code and output, or a status line)
  <dir>/inbox/<name>       files the guest SENT
  <dir>/outbox/<name>      files the guest can be told to PUT
Binds to one address (UTM's host side of the shared network) and requires a token in every
path. tools/vmlink/q.py queues one command and waits for its result.

    python tools/vmlink/server.py --bind 192.168.64.1 --port 8765 --dir vendor/vm_link --token T
"""
import argparse
import http.server
import pathlib
import re
import urllib.parse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--token", required=True)
    a = ap.parse_args()
    root = pathlib.Path(a.dir)
    for sub in ("queue", "results", "inbox", "outbox"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    prefix = "/" + a.token

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            with open(root / "server.log", "a") as log:
                log.write("%s %s\n" % (self.address_string(), fmt % args))

        def reply(self, code, body=b"", ctype="application/octet-stream"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)

        def route(self):
            u = urllib.parse.urlparse(self.path)
            if not u.path.startswith(prefix + "/"):
                return None, None
            return u.path[len(prefix) + 1:], urllib.parse.parse_qs(u.query)

        def do_GET(self):
            path, q = self.route()
            if path is None:
                return self.reply(404)
            return self.serve(path)

        def serve(self, path):
            if path == "next":
                pending = sorted((root / "queue").glob("*.cmd"), key=lambda p: int(p.stem))
                for p in pending:
                    if not (root / "results" / (p.stem + ".txt")).exists() and not p.with_suffix(".sent").exists():
                        p.with_suffix(".sent").write_text("")
                        return self.reply(200, ("%s\r\n%s" % (p.stem, p.read_text().strip())).encode("latin1"), "text/plain")
                return self.reply(200, b"", "text/plain")
            m = re.fullmatch(r"get/([\w.\-]+)", path)
            if m and (root / "outbox" / m.group(1)).is_file():
                return self.reply(200, (root / "outbox" / m.group(1)).read_bytes())
            return self.reply(404)

        def do_POST(self):
            path, q = self.route()
            if path is None:
                return self.reply(404)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if path == "next":
                return self.serve(path)
            m = re.fullmatch(r"result/(\d+)", path)
            if m:
                (root / "results" / (m.group(1) + ".txt")).write_bytes(body)
                return self.reply(200, b"ok", "text/plain")
            m = re.fullmatch(r"file/(\d+)", path)
            if m:
                name = re.sub(r"[^\w.\-]", "_", q.get("name", ["upload"])[0].replace("\\", "/").split("/")[-1])
                (root / "inbox" / name).write_bytes(body)
                (root / "results" / (m.group(1) + ".txt")).write_text("received %s, %d bytes\n" % (name, len(body)))
                return self.reply(200, b"ok", "text/plain")
            return self.reply(404)

    http.server.ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
