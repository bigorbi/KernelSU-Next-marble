"""Read-only ELF helpers for optional binary investigation; no release gate."""

import pathlib
import struct
import collections


class Elf:
    def __init__(self, p):
        self.d = pathlib.Path(p).read_bytes()
        h = struct.unpack_from("<16sHHIQQQIHHHHHH", self.d)
        if self.d[:6] != b"\x7fELF\x02\x01" or h[1] != 1 or h[2] != 183:
            raise ValueError("Expected ARM64 ELF64 relocatable module")
        self.ss = [
            struct.unpack_from("<IIQQQQIIQQ", self.d, h[6] + h[11] * i)
            for i in range(h[12])
        ]

        def blob(s):
            return self.d[s[4] : s[4] + s[5]]

        def string(b, n):
            return b[n:].split(b"\0", 1)[0].decode(errors="replace")

        self.names = [string(blob(self.ss[h[13]]), s[0]) for s in self.ss]
        tab = self.ss[self.names.index(".symtab")]
        strings = blob(self.ss[tab[6]])
        self.sy = []
        for o in range(tab[4], tab[4] + tab[5], 24):
            n, t, v, s, a, z = struct.unpack_from("<IBBHQQ", self.d, o)
            self.sy.append(
                dict(
                    name=string(strings, n),
                    type=t & 15,
                    binding=t >> 4,
                    section=s,
                    value=a,
                    size=z,
                )
            )
        self.byname = {s["name"]: s for s in self.sy}
        self.rel = collections.defaultdict(list)
        for ss in self.ss:
            if ss[1] == 4:
                for o in range(ss[4], ss[4] + ss[5], 24):
                    a, i, v = struct.unpack_from("<QQq", self.d, o)
                    self.rel[ss[7]].append((a, i & 0xFFFFFFFF, self.sy[i >> 32], v))

    def body(self, name):
        s = self.byname[name]
        sec = self.ss[s["section"]]
        start = s["value"]
        if start + s["size"] > sec[5]:
            raise ValueError("Function outside section")
        return self.d[sec[4] + start : sec[4] + start + s["size"]]

    def refs(self, name):
        s = self.byname[name]
        return [
            (a - s["value"], k, t, v)
            for a, k, t, v in self.rel[s["section"]]
            if s["value"] <= a < s["value"] + s["size"]
        ]
