"""Decompressors for Godot's ``Compression`` modes, as used by compressed binary resources (``RSCC``) and other
``FileAccessCompressed`` files. Stdlib only.

Modes (``core/io/compression.h``): 0 FastLZ, 1 Deflate (zlib stream), 2 Zstandard, 3 Gzip, 4 Brotli (Godot 4 only,
decompression only). Deflate and Gzip use :mod:`zlib`. Zstandard uses ``compression.zstd`` (Python 3.14+) or the
``zstandard`` package when importable, else the pure-Python decoder below (RFC 8878; Godot writes small frames, one
per 4 KiB block, so speed is fine). Brotli needs the ``brotli`` package (reported as an error when missing).
FastLZ levels 1 and 2 are decoded in Python (format of ``thirdparty/misc/fastlz.c``, MIT).
"""

from __future__ import annotations

import zlib

MODES = {0: "fastlz", 1: "deflate", 2: "zstd", 3: "gzip", 4: "brotli"}


class CodecError(ValueError):
    pass


def decompress(mode: int, data: bytes, size: int) -> bytes:
    """One block compressed with Godot ``Compression::Mode`` ``mode`` -> ``size`` bytes."""
    if mode == 0:
        if size < 16:  # Godot pads inputs under 16 bytes to 16 before FastLZ
            return fastlz_decompress(data, 16)[:size]
        out = fastlz_decompress(data, size)
    elif mode == 1:
        out = zlib.decompress(data)
    elif mode == 3:
        out = zlib.decompress(data, 31)
    elif mode == 2:
        out = zstd_decompress(data)
    elif mode == 4:
        try:
            import brotli  # type: ignore
        except ImportError as exc:
            raise CodecError("Brotli-compressed data needs the 'brotli' Python package") from exc
        out = brotli.decompress(data)
    else:
        raise CodecError(f"unknown compression mode {mode}")
    if len(out) < size:
        raise CodecError(f"{MODES.get(mode, mode)} block gave {len(out)} bytes, expected {size}")
    return out[:size]


# --- FastLZ -------------------------------------------------------------------------------------

def fastlz_decompress(src: bytes, maxout: int) -> bytes:
    if not src:
        return b""
    level = (src[0] >> 5) + 1
    if level not in (1, 2):
        raise CodecError(f"FastLZ level {level}")
    out = bytearray()
    n = len(src)
    ip = 1
    ctrl = src[0] & 31
    while True:
        if ctrl >= 32:
            length = (ctrl >> 5) - 1
            ofs = (ctrl & 31) << 8
            if level == 1:
                if length == 6:
                    length += src[ip]
                    ip += 1
                ref = len(out) - ofs - src[ip] - 1
                ip += 1
            else:
                if length == 6:
                    while True:
                        code = src[ip]
                        ip += 1
                        length += code
                        if code != 255:
                            break
                code = src[ip]
                ip += 1
                ref = len(out) - ofs - code - 1
                if code == 255 and ofs == (31 << 8):
                    ofs = (src[ip] << 8) + src[ip + 1]
                    ip += 2
                    ref = len(out) - ofs - 8191 - 1
            length += 3
            if ref < 0:
                raise CodecError("FastLZ reference before the start of the output")
            for k in range(length):  # byte by byte: the match may overlap the output
                out.append(out[ref + k])
        else:
            ctrl += 1
            out += src[ip:ip + ctrl]
            ip += ctrl
        if ip >= n or len(out) >= maxout:
            break
        ctrl = src[ip]
        ip += 1
    return bytes(out)


# --- Zstandard ----------------------------------------------------------------------------------

def zstd_decompress(data: bytes) -> bytes:
    fast = _zstd_fast()
    if fast is not None:
        return fast(data)
    return ZstdDecoder().decompress(data)


_FAST: list = []


def _zstd_fast():
    if not _FAST:
        func = None
        try:
            from compression import zstd as _z  # type: ignore  # Python 3.14+

            func = _z.decompress
        except ImportError:
            try:
                import zstandard as _zs  # type: ignore

                def func(b: bytes, _d=_zs.ZstdDecompressor()) -> bytes:
                    return _d.decompressobj().decompress(b)
            except ImportError:
                func = None
        _FAST.append(func)
    return _FAST[0]


LL_BASE = [(i, 0) for i in range(16)] + [
    (16, 1), (18, 1), (20, 1), (22, 1), (24, 2), (28, 2), (32, 3), (40, 3), (48, 4), (64, 6), (128, 7), (256, 8),
    (512, 9), (1024, 10), (2048, 11), (4096, 12), (8192, 13), (16384, 14), (32768, 15), (65536, 16)]
ML_BASE = [(i + 3, 0) for i in range(32)] + [
    (35, 1), (37, 1), (39, 1), (41, 1), (43, 2), (47, 2), (51, 3), (59, 3), (67, 4), (83, 4), (99, 5), (131, 7),
    (259, 8), (515, 9), (1027, 10), (2051, 11), (4099, 12), (8195, 13), (16387, 14), (32771, 15), (65539, 16)]
LL_DEFAULT = (6, [4, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 1, 1, 1, 1, 1,
                  -1, -1, -1, -1])
ML_DEFAULT = (6, [1, 4, 3, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                  1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1, -1, -1])
OF_DEFAULT = (5, [1, 1, 1, 1, 1, 1, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1])
MAGIC = 0xFD2FB528


class _Fwd:
    """Forward little-endian bit reader (FSE table descriptions)."""

    def __init__(self, data: bytes, start: int):
        self.d = data
        self.bit = start * 8

    def read(self, n: int) -> int:
        if n == 0:
            return 0
        lo = self.bit >> 3
        hi = (self.bit + n + 7) >> 3
        if hi > len(self.d):
            raise CodecError("zstd: truncated table description")
        v = int.from_bytes(self.d[lo:hi], "little") >> (self.bit & 7)
        self.bit += n
        return v & ((1 << n) - 1)

    def byte_pos(self) -> int:
        return (self.bit + 7) >> 3


class _Back:
    """Backward bit stream (Huffman and FSE payloads): the bits read in order, as a '0'/'1' string."""

    __slots__ = ("s", "p")

    def __init__(self, chunk: bytes):
        if not chunk or chunk[-1] == 0:
            raise CodecError("zstd: bitstream without its end marker")
        self.s = format(int.from_bytes(chunk, "little"), "b")[1:]  # drop the marker bit
        self.p = 0

    def read(self, n: int) -> int:
        if n == 0:
            return 0
        p = self.p
        self.p = p + n
        chunk = self.s[p:p + n]
        if len(chunk) < n:
            chunk = chunk.ljust(n, "0")  # past the start of the stream: zeros
        return int(chunk, 2)

    def left(self) -> int:
        return len(self.s) - self.p


def _fse_table(norm: list[int], acc: int) -> tuple[list, list, list]:
    size = 1 << acc
    symbols = [0] * size
    state_desc = [0] * len(norm)
    high = size
    for s, f in enumerate(norm):
        if f == -1:
            high -= 1
            symbols[high] = s
            state_desc[s] = 1
    step = (size >> 1) + (size >> 3) + 3
    mask = size - 1
    pos = 0
    for s, f in enumerate(norm):
        if f <= 0:
            continue
        state_desc[s] = f
        for _ in range(f):
            symbols[pos] = s
            pos = (pos + step) & mask
            while pos >= high:
                pos = (pos + step) & mask
    if pos != 0:
        raise CodecError("zstd: bad FSE distribution")
    nbits = [0] * size
    base = [0] * size
    for i in range(size):
        s = symbols[i]
        nxt = state_desc[s]
        state_desc[s] += 1
        nb = acc - (nxt.bit_length() - 1)
        nbits[i] = nb
        base[i] = (nxt << nb) - size
    return symbols, nbits, base


def _fse_read_table(data: bytes, pos: int, max_acc: int) -> tuple[tuple, int]:
    """FSE table description at ``pos`` -> ((symbols, nbits, base, acc), position after it)."""
    br = _Fwd(data, pos)
    acc = br.read(4) + 5
    if acc > max_acc:
        raise CodecError(f"zstd: FSE accuracy {acc} over {max_acc}")
    remaining = 1 << acc
    norm: list[int] = []
    while remaining > 0:
        bits = (remaining + 1).bit_length()
        val = br.read(bits)
        lower = (1 << (bits - 1)) - 1
        threshold = (1 << bits) - 1 - (remaining + 1)
        if (val & lower) < threshold:
            br.bit -= 1
            val &= lower
        elif val > lower:
            val -= threshold
        proba = val - 1
        remaining -= -proba if proba < 0 else proba
        norm.append(proba)
        if proba == 0:
            while True:
                rep = br.read(2)
                norm.extend([0] * rep)
                if rep != 3:
                    break
        if len(norm) > 256:
            raise CodecError("zstd: too many FSE symbols")
    if remaining != 0:
        raise CodecError("zstd: FSE probabilities do not add up")
    symbols, nbits, base = _fse_table(norm, acc)
    return (symbols, nbits, base, acc), br.byte_pos()


def _rle_table(sym: int) -> tuple:
    return [sym], [0], [0], 0


_PREDEF: dict[str, tuple] = {}


def _predefined(kind: str) -> tuple:
    if kind not in _PREDEF:
        acc, norm = {"ll": LL_DEFAULT, "ml": ML_DEFAULT, "of": OF_DEFAULT}[kind]
        _PREDEF[kind] = (*_fse_table(norm, acc), acc)
    return _PREDEF[kind]


class ZstdDecoder:
    """Pure-Python Zstandard frame decoder (no dictionaries; checksums are not verified)."""

    def decompress(self, data: bytes) -> bytes:
        out = bytearray()
        pos = 0
        n = len(data)
        while pos < n:
            if n - pos < 4:
                raise CodecError("zstd: truncated frame")
            magic = int.from_bytes(data[pos:pos + 4], "little")
            if magic & 0xFFFFFFF0 == 0x184D2A50:  # skippable frame
                size = int.from_bytes(data[pos + 4:pos + 8], "little")
                pos += 8 + size
                continue
            if magic != MAGIC:
                raise CodecError(f"zstd: bad magic {magic:#x}")
            pos = self._frame(data, pos + 4, out)
        return bytes(out)

    def _frame(self, data: bytes, pos: int, out: bytearray) -> int:
        desc = data[pos]
        pos += 1
        fcs_flag = desc >> 6
        single = (desc >> 5) & 1
        checksum = (desc >> 2) & 1
        dict_flag = desc & 3
        if not single:
            pos += 1  # window descriptor
        did_size = (0, 1, 2, 4)[dict_flag]
        if did_size and int.from_bytes(data[pos:pos + did_size], "little"):
            raise CodecError("zstd: frames that need a dictionary are not supported")
        pos += did_size
        pos += (1 if single else 0, 2, 4, 8)[fcs_flag]
        frame_start = len(out)
        self.rep = [1, 4, 8]
        self.huff = None
        self.tables = {"ll": None, "of": None, "ml": None}
        while True:
            hdr = int.from_bytes(data[pos:pos + 3], "little")
            pos += 3
            last = hdr & 1
            btype = (hdr >> 1) & 3
            bsize = hdr >> 3
            if btype == 0:
                out += data[pos:pos + bsize]
                pos += bsize
            elif btype == 1:
                out += bytes([data[pos]]) * bsize
                pos += 1
            elif btype == 2:
                self._block(data[pos:pos + bsize], out, frame_start)
                pos += bsize
            else:
                raise CodecError("zstd: reserved block type")
            if last:
                break
        if checksum:
            pos += 4
        return pos

    # --- literals
    def _huff_table(self, data: bytes, pos: int) -> tuple[tuple, int]:
        hb = data[pos]
        pos += 1
        if hb < 128:  # FSE-compressed weights
            end = pos + hb
            (symbols, nbits, base, acc), p = _fse_read_table(data, pos, 6)
            br = _Back(data[p:end])
            s1 = br.read(acc)
            s2 = br.read(acc)
            weights = []
            while True:
                weights.append(symbols[s1])
                s1 = base[s1] + br.read(nbits[s1])
                if br.left() < 0:
                    weights.append(symbols[s2])
                    break
                weights.append(symbols[s2])
                s2 = base[s2] + br.read(nbits[s2])
                if br.left() < 0:
                    weights.append(symbols[s1])
                    break
                if len(weights) > 255:
                    raise CodecError("zstd: too many Huffman weights")
            pos = end
        else:
            count = hb - 127
            nbytes = (count + 1) // 2
            raw = data[pos:pos + nbytes]
            weights = []
            for b in raw:
                weights += [b >> 4, b & 15]
            weights = weights[:count]
            pos += nbytes
        total = sum(1 << (w - 1) for w in weights if w)
        if total == 0:
            raise CodecError("zstd: empty Huffman weights")
        max_bits = total.bit_length()
        left = (1 << max_bits) - total
        if left & (left - 1):
            raise CodecError("zstd: Huffman weights do not complete a tree")
        weights.append(left.bit_length())
        if max_bits > 11:
            raise CodecError("zstd: Huffman code over 11 bits")
        table: list = [None] * (1 << max_bits)
        p = 0
        for w in range(1, max_bits + 1):
            span = 1 << (w - 1)
            nb = max_bits + 1 - w
            for sym, sw in enumerate(weights):
                if sw == w:
                    entry = (sym, nb)
                    for k in range(p, p + span):
                        table[k] = entry
                    p += span
        return (table, max_bits), pos

    def _huff_stream(self, chunk: bytes, count: int, out: bytearray) -> None:
        table, max_bits = self.huff
        s = format(int.from_bytes(chunk, "little"), "b")[1:] if chunk and chunk[-1] else None
        if s is None:
            raise CodecError("zstd: Huffman stream without its end marker")
        s += "0" * max_bits
        p = 0
        append = out.append
        for _ in range(count):
            sym, nb = table[int(s[p:p + max_bits], 2)]
            append(sym)
            p += nb
        if p != len(s) - max_bits:
            raise CodecError("zstd: Huffman stream size mismatch")

    def _literals(self, blk: bytes) -> tuple[bytes, int]:
        b0 = blk[0]
        ltype = b0 & 3
        sf = (b0 >> 2) & 3
        if ltype in (0, 1):
            if sf in (0, 2):
                regen, pos = b0 >> 3, 1
            elif sf == 1:
                regen, pos = (b0 >> 4) + (blk[1] << 4), 2
            else:
                regen, pos = (b0 >> 4) + (blk[1] << 4) + (blk[2] << 12), 3
            if ltype == 0:
                return bytes(blk[pos:pos + regen]), pos + regen
            return bytes([blk[pos]]) * regen, pos + 1
        if sf in (0, 1):
            v = int.from_bytes(blk[0:3], "little")
            regen, csize, pos = (v >> 4) & 0x3FF, (v >> 14) & 0x3FF, 3
        elif sf == 2:
            v = int.from_bytes(blk[0:4], "little")
            regen, csize, pos = (v >> 4) & 0x3FFF, (v >> 18) & 0x3FFF, 4
        else:
            v = int.from_bytes(blk[0:5], "little")
            regen, csize, pos = (v >> 4) & 0x3FFFF, (v >> 22) & 0x3FFFF, 5
        streams = 1 if sf == 0 else 4
        end = pos + csize
        if ltype == 2:
            self.huff, pos = self._huff_table(blk, pos)
        elif self.huff is None:
            raise CodecError("zstd: treeless literals without a previous Huffman table")
        out = bytearray()
        if streams == 1:
            self._huff_stream(blk[pos:end], regen, out)
        else:
            s1, s2, s3 = (int.from_bytes(blk[pos + 2 * i:pos + 2 * i + 2], "little") for i in range(3))
            p = pos + 6
            per = (regen + 3) // 4
            sizes = (s1, s2, s3, end - p - s1 - s2 - s3)
            for i, sz in enumerate(sizes):
                cnt = per if i < 3 else regen - 3 * per
                self._huff_stream(blk[p:p + sz], cnt, out)
                p += sz
        return bytes(out), end

    # --- sequences
    def _seq_table(self, kind: str, mode: int, blk: bytes, pos: int, max_sym: int, max_acc: int) -> int:
        if mode == 0:
            self.tables[kind] = _predefined(kind)
        elif mode == 1:
            self.tables[kind] = _rle_table(blk[pos])
            pos += 1
        elif mode == 2:
            t, pos = _fse_read_table(blk, pos, max_acc)
            self.tables[kind] = t
        elif self.tables[kind] is None:
            raise CodecError(f"zstd: repeat mode for {kind} without a previous table")
        return pos

    def _block(self, blk: bytes, out: bytearray, frame_start: int) -> None:
        lits, pos = self._literals(blk)
        b0 = blk[pos]
        if b0 == 0:
            out += lits
            return
        if b0 < 128:
            nseq, pos = b0, pos + 1
        elif b0 < 255:
            nseq, pos = ((b0 - 128) << 8) + blk[pos + 1], pos + 2
        else:
            nseq, pos = blk[pos + 1] + (blk[pos + 2] << 8) + 0x7F00, pos + 3
        modes = blk[pos]
        pos += 1
        pos = self._seq_table("ll", modes >> 6, blk, pos, 35, 9)
        pos = self._seq_table("of", (modes >> 4) & 3, blk, pos, 31, 8)
        pos = self._seq_table("ml", (modes >> 2) & 3, blk, pos, 52, 9)
        br = _Back(blk[pos:])
        ll_sym, ll_nb, ll_base, ll_acc = self.tables["ll"]
        of_sym, of_nb, of_base, of_acc = self.tables["of"]
        ml_sym, ml_nb, ml_base, ml_acc = self.tables["ml"]
        read = br.read
        ll_s = read(ll_acc)
        of_s = read(of_acc)
        ml_s = read(ml_acc)
        rep = self.rep
        lp = 0
        for i in range(nseq):
            of_code = of_sym[of_s]
            ll_code = ll_sym[ll_s]
            ml_code = ml_sym[ml_s]
            if of_code > 31:
                raise CodecError("zstd: offset code over 31")
            offset_value = (1 << of_code) + read(of_code)
            mb, mbits = ML_BASE[ml_code]
            ml = mb + read(mbits)
            lb, lbits = LL_BASE[ll_code]
            ll = lb + read(lbits)
            if offset_value > 3:
                offset = offset_value - 3
                rep[2], rep[1], rep[0] = rep[1], rep[0], offset
            else:
                idx = offset_value + (1 if ll == 0 else 0)
                if idx == 1:
                    offset = rep[0]
                elif idx == 2:
                    offset = rep[1]
                    rep[1], rep[0] = rep[0], offset
                elif idx == 3:
                    offset = rep[2]
                    rep[2], rep[1], rep[0] = rep[1], rep[0], offset
                else:
                    offset = rep[0] - 1
                    if offset <= 0:
                        raise CodecError("zstd: invalid repeat offset")
                    rep[2], rep[1], rep[0] = rep[1], rep[0], offset
            if i != nseq - 1:
                ll_s = ll_base[ll_s] + read(ll_nb[ll_s])
                ml_s = ml_base[ml_s] + read(ml_nb[ml_s])
                of_s = of_base[of_s] + read(of_nb[of_s])
            if ll:
                out += lits[lp:lp + ll]
                lp += ll
            start = len(out) - offset
            if start < frame_start:
                raise CodecError("zstd: match offset before the start of the frame")
            if offset >= ml:
                out += out[start:start + ml]
            else:  # overlapping copy: repeat the period
                period = bytes(out[start:])
                reps, rest = divmod(ml, offset)
                out += period * reps + period[:rest]
        if br.left() != 0:
            raise CodecError("zstd: sequence bitstream not fully consumed")
        out += lits[lp:]
