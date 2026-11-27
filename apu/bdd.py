"""A small reduced ordered binary decision diagram (BDD) package.

Nodes are integers: 0 is FALSE, 1 is TRUE, others index a table of (variable, low, high).
Lower variable numbers sit nearer the root. `isop` gives an irredundant sum of products
(Minato-Morreale), which is what synthesis needs from a function too wide to tabulate.
"""


class BDD:
    FALSE, TRUE = 0, 1

    def __init__(self):
        self.nodes = [(None, None, None), (None, None, None)]
        self.unique = {}
        self.cache = {}
        self.names = []
        self.index = {}

    # -- construction ------------------------------------------------------------------------
    def var(self, name):
        """The BDD of a variable, creating it (after all existing ones) if new."""
        if name not in self.index:
            self.index[name] = len(self.names)
            self.names.append(name)
        return self.mk(self.index[name], 0, 1)

    def mk(self, v, lo, hi):
        if lo == hi:
            return lo
        key = (v, lo, hi)
        node = self.unique.get(key)
        if node is None:
            node = len(self.nodes)
            self.nodes.append(key)
            self.unique[key] = node
        return node

    def top(self, *fs):
        return min((self.nodes[f][0] for f in fs if f > 1), default=None)

    def cof(self, f, v):
        if f <= 1 or self.nodes[f][0] != v:
            return f, f
        _, lo, hi = self.nodes[f]
        return lo, hi

    def ite(self, f, g, h):
        if f == 1:
            return g
        if f == 0:
            return h
        if g == h:
            return g
        if g == 1 and h == 0:
            return f
        key = ("ite", f, g, h)
        r = self.cache.get(key)
        if r is not None:
            return r
        v = self.top(f, g, h)
        f0, f1 = self.cof(f, v)
        g0, g1 = self.cof(g, v)
        h0, h1 = self.cof(h, v)
        r = self.mk(v, self.ite(f0, g0, h0), self.ite(f1, g1, h1))
        self.cache[key] = r
        return r

    def NOT(self, f):
        return self.ite(f, 0, 1)

    def AND(self, f, g):
        return self.ite(f, g, 0)

    def OR(self, f, g):
        return self.ite(f, 1, g)

    def XOR(self, f, g):
        return self.ite(f, self.NOT(g), g)

    def EQ(self, f, g):
        return self.ite(f, g, self.NOT(g))

    # -- inspection --------------------------------------------------------------------------
    def support(self, f):
        seen, out, stack = set(), set(), [f]
        while stack:
            n = stack.pop()
            if n <= 1 or n in seen:
                continue
            seen.add(n)
            v, lo, hi = self.nodes[n]
            out.add(self.names[v])
            stack += [lo, hi]
        return out

    def evaluate(self, f, env):
        while f > 1:
            v, lo, hi = self.nodes[f]
            f = hi if env[self.names[v]] else lo
        return f

    def isop(self, lower, upper=None):
        """[{name: 0/1}] covering every point of `lower` and none outside `upper`."""
        memo = {}

        def rec(L, U):
            if L == 0:
                return [], 0
            if U == 1:
                return [{}], 1
            key = (L, U)
            if key in memo:
                return memo[key]
            v = self.top(L, U)
            L0, L1 = self.cof(L, v)
            U0, U1 = self.cof(U, v)
            c0, f0 = rec(self.AND(L0, self.NOT(U1)), U0)
            c1, f1 = rec(self.AND(L1, self.NOT(U0)), U1)
            Lr = self.OR(self.AND(L0, self.NOT(f0)), self.AND(L1, self.NOT(f1)))
            cr, fr = rec(Lr, self.AND(U0, U1))
            name = self.names[v]
            cover = [dict(c, **{name: 0}) for c in c0] + [dict(c, **{name: 1}) for c in c1] + cr
            f = self.ite(self.mk(v, 0, 1), self.OR(f1, fr), self.OR(f0, fr))
            memo[key] = (cover, f)
            return cover, f
        return rec(lower, lower if upper is None else upper)[0]
