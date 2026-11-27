"""Queue one command for the guest agent and wait for its result.

    python tools/vmlink/q.py vendor/vm_link 'RUN dir C:\\'
    python tools/vmlink/q.py vendor/vm_link 'SEND C:\\max2work\\probes\\p01_comb.pof'
"""
import pathlib
import sys
import time


def queue(root, line, timeout=600):
    root = pathlib.Path(root)
    (root / "queue").mkdir(parents=True, exist_ok=True)
    taken = [int(p.stem) for p in (root / "queue").glob("*.cmd")]
    n = max(taken, default=0) + 1
    (root / "queue" / ("%d.cmd" % n)).write_text(line + "\n")
    result = root / "results" / ("%d.txt" % n)
    end = time.time() + timeout
    while time.time() < end:
        if result.exists():
            time.sleep(0.2)
            return result.read_bytes().decode("latin1")
        time.sleep(0.5)
    return None


if __name__ == "__main__":
    out = queue(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 600)
    if out is None:
        sys.exit("no result yet (is the agent running in the guest?)")
    sys.stdout.write(out)
