"""MAX+PLUS II simulator table files (.tbl): reading, and replaying them against apu's simulator.

A .tbl is the vendor simulator's text output: GROUP CREATE lines, INPUTS / OUTPUTS / BURIED
lists, RADIX, then PATTERN rows `time> inputs = outputs buried` (one value per name, groups in
the radix, state machines as state names), ending with `;`.
"""
import re


class Table:
    def __init__(self, groups, inputs, outputs, buried, radix, rows):
        self.groups, self.inputs, self.outputs, self.buried = groups, inputs, outputs, buried
        self.radix, self.rows = radix, rows             # rows: [(time, {name: value string})]


def _statements(text):
    body = re.sub(r"%[^%]*%", " ", text)                 # comments
    return body


def parse(text: str) -> Table:
    body = _statements(text.replace("\r", ""))
    groups = {}
    for m in re.finditer(r"GROUP CREATE\s+(\S+)\s*=\s*([^;]*);", body):
        groups[m.group(1)] = m.group(2).split()
    lists = {}
    for key in ("INPUTS", "OUTPUTS", "BURIED"):
        m = re.search(r"\b%s\b([^;]*);" % key, body)
        lists[key] = m.group(1).split() if m else []
    m = re.search(r"\bRADIX\s+(\w+)\s*;", body)
    radix = {"HEX": 16, "BIN": 2, "OCT": 8, "DEC": 10}.get(m.group(1).upper(), 16) if m else 16
    pattern = body[body.index("PATTERN") + len("PATTERN"):]
    names = lists["INPUTS"] + lists["OUTPUTS"] + lists["BURIED"]
    rows = []
    for line in pattern.splitlines():
        m = re.match(r"\s*([\d.]+)>\s*(.*)$", line)
        if not m:
            continue
        fields = m.group(2).replace("=", " ").split()
        if len(fields) < len(names):
            continue
        rows.append((float(m.group(1)), dict(zip(names, fields))))
    return Table(groups, lists["INPUTS"], lists["OUTPUTS"], lists["BURIED"], radix, rows)


def compare(table: Table, sim, rename=lambda name: name, signals=None):
    """Replay the table's inputs into `sim` (an ahdl_eval.Simulator) and compare the signals
    named in `signals` (default: the outputs) once they have settled, i.e. on the last row before
    each input change. Returns (checks, [(time, name, expected, got)])."""
    d = sim.design
    signals = signals or table.outputs

    def bits_of(name):
        members = table.groups.get(name, [name])
        return [rename(m) for m in members]

    def current(name):
        machine = rename(name)
        if machine in d.machines:
            state = sim.state(machine)
            return state.split("/")[-1] if isinstance(state, str) else str(state)
        value = 0
        for bit in bits_of(name):
            v = sim.env.get(bit, sim.env.get(bit + ".q", None))
            if v is None and bit in sim.q:
                v = sim.q[bit]
            value = value << 1 | (v or 0)
        return value

    def expected(name, text):
        if rename(name) in d.machines:
            return text.split("|")[-1]
        return None if "X" in text.upper() else int(text, table.radix)

    mismatches, checks = [], 0
    last_inputs = None
    for i, (time, row) in enumerate(table.rows):
        ins = {n: row[n] for n in table.inputs}
        if any("X" in v.upper() for v in ins.values()):
            break
        if ins != last_inputs:
            sim.set(**{rename(n): int(v, table.radix) for n, v in ins.items()})
            last_inputs = ins
        nxt = table.rows[i + 1][1] if i + 1 < len(table.rows) else None
        if nxt is not None and {n: nxt[n] for n in table.inputs} == ins:
            continue                                     # outputs still settling
        for name in signals:
            want = expected(name, row[name])
            if want is None:
                continue
            checks += 1
            got = current(name)
            if got != want:
                mismatches.append((time, name, want, got))
    return checks, mismatches
