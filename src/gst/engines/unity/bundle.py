"""Streaming UnityFS bundles: decompress blocks on demand instead of the whole bundle in RAM.

UnityPy 1.25 ``BundleFile.read_fs`` joins every decompressed block into one ``bytes`` and then copies
each node again (``File.read_files``), so a 2 GB LZ4 map bundle of PEAK needs ~2x its uncompressed size
in RAM. :class:`LazyBundleFile` keeps UnityPy's parsing of the header and directory (copied from
UnityPy, MIT) but serves the data through :class:`BlocksIO`, which decompresses a block when a read
touches it and keeps a bounded LRU of decompressed blocks. Each node (CAB serialized file, .resS) is a
:class:`SliceIO` view, so object data is read only when an exporter asks for it.

A bundle whose single block is bigger than the RAM we can spare (old LZMA bundles compress as one
block) raises :class:`BundleTooLarge`; extractors turn that into ``unsupported`` ("fallback assetripper").
"""

from __future__ import annotations

import bisect
import io
import struct
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from gst.engines.unity.mem import available_ram

CACHE_BYTES = 256 << 20  # decompressed blocks kept per bundle (LRU); a working set, not a ceiling
RAM_FRACTION = 0.5       # a single block must fit in this fraction of the available RAM


class BundleTooLarge(RuntimeError):
    """A block cannot be decompressed in the RAM available (fallback: AssetRipper per bundle)."""


@dataclass
class BundleHeader:
    signature: str
    version: int
    version_player: str
    version_engine: str
    flags: int
    blocks: list[tuple[int, int, int]]          # (uncompressed, compressed, flags)
    nodes: list[tuple[int, int, int, str]]      # (offset, size, flags, path)
    data_start: int                             # file offset of the first block

    @property
    def uncompressed_size(self) -> int:
        return sum(b[0] for b in self.blocks)

    @property
    def largest_block(self) -> int:
        return max((b[0] for b in self.blocks), default=0)

    @property
    def compression(self) -> str:
        names = {0: "none", 1: "lzma", 2: "lz4", 3: "lz4hc", 4: "lzham"}
        kinds = sorted({names.get(b[2] & 0x3F, str(b[2] & 0x3F)) for b in self.blocks})
        return "+".join(kinds) or "none"


def _cstr(fh) -> str:
    out = bytearray()
    while True:
        c = fh.read(1)
        if not c or c == b"\0":
            return out.decode("utf-8", "replace")
        out += c


def _decompress(data: bytes, size: int, flags: int) -> bytes:
    from UnityPy.enums import CompressionFlags
    from UnityPy.helpers import CompressionHelper

    comp = CompressionFlags(flags & 0x3F)
    if comp == CompressionFlags.NONE:
        return data
    if comp not in CompressionHelper.DECOMPRESSION_MAP:
        raise ValueError(f"unknown bundle compression {comp}")
    return CompressionHelper.DECOMPRESSION_MAP[comp](data, size)


def _old_flags(engine: str) -> bool:
    """Unity versions where 0x200 meant something else (UnityPy's ArchiveFlagsOld rule)."""
    import re

    m = re.match(r"(\d+)\.(\d+)\.(\d+)", engine or "")
    if not m:
        return False
    v = tuple(int(x) for x in m.groups())
    return (v < (2020,) or (v[0] == 2020 and v < (2020, 3, 34)) or (v[0] == 2021 and v < (2021, 3, 2))
            or (v[0] == 2022 and v < (2022, 1, 1)))


def read_header(path: str | Path) -> BundleHeader:
    """Header, block table and directory of a UnityFS bundle, reading only the first/last KBs."""
    with open(path, "rb") as fh:
        signature = _cstr(fh)
        if signature != "UnityFS":
            raise ValueError(f"not a UnityFS bundle ({signature!r})")
        version = struct.unpack(">I", fh.read(4))[0]
        player, engine = _cstr(fh), _cstr(fh)
        _size, csize, usize, flags = struct.unpack(">qIII", fh.read(20))
        old = _old_flags(engine)
        if flags & (0x200 if old else 0x1400):
            raise ValueError("encrypted (UnityCN) bundle: no published key handling")
        if version >= 7 or engine.startswith("2019.4"):
            fh.seek((fh.tell() + 15) // 16 * 16)
        start = fh.tell()
        if flags & 0x80:  # blocks info at the end
            fh.seek(-csize, 2)
            info = fh.read(csize)
            fh.seek(start)
        else:
            info = fh.read(csize)
        info = _decompress(info, usize, flags)
        if not old and flags & 0x200:  # BlockInfoNeedPaddingAtStart
            fh.seek((fh.tell() + 15) // 16 * 16)
        data_start = fh.tell()
    pos = 16
    (nblocks,) = struct.unpack_from(">i", info, pos)
    pos += 4
    blocks = []
    for _ in range(nblocks):
        u, c, f = struct.unpack_from(">IIH", info, pos)
        pos += 10
        blocks.append((u, c, f))
    (nnodes,) = struct.unpack_from(">i", info, pos)
    pos += 4
    nodes = []
    for _ in range(nnodes):
        off, size, f = struct.unpack_from(">qqI", info, pos)
        pos += 20
        end = info.index(b"\0", pos)
        nodes.append((off, size, f, info[pos:end].decode("utf-8", "replace")))
        pos = end + 1
    return BundleHeader(signature, version, player, engine, flags, blocks, nodes, data_start)


class BlocksIO:
    """Random access to the concatenated uncompressed blocks of one bundle (thread-unsafe)."""

    def __init__(self, path: str | Path, header: BundleHeader, *, cache_bytes: int = CACHE_BYTES):
        self.path = str(path)
        self.fh = open(self.path, "rb")
        self.blocks = header.blocks
        self.u_starts, self.c_starts = [], []
        u = c = 0
        for us, cs, _f in header.blocks:
            self.u_starts.append(u)
            self.c_starts.append(header.data_start + c)
            u += us
            c += cs
        self.length = u
        self.cache: OrderedDict[int, bytes] = OrderedDict()
        self.cache_bytes = cache_bytes
        self.cached = 0
        budget = available_ram() * RAM_FRACTION
        if header.largest_block > budget:
            raise BundleTooLarge(f"{Path(self.path).name}: one block of {header.largest_block} bytes "
                                 f"({header.compression}) does not fit in {int(budget)} bytes of free RAM")

    def _block(self, i: int) -> bytes:
        hit = self.cache.get(i)
        if hit is not None:
            self.cache.move_to_end(i)
            return hit
        usize, csize, flags = self.blocks[i]
        self.fh.seek(self.c_starts[i])
        data = _decompress(self.fh.read(csize), usize, flags)
        self.cache[i] = data
        self.cached += len(data)
        while self.cached > self.cache_bytes and len(self.cache) > 1:
            _k, old = self.cache.popitem(last=False)
            self.cached -= len(old)
        return data

    def read_at(self, pos: int, n: int) -> bytes:
        if n <= 0 or pos >= self.length:
            return b""
        n = min(n, self.length - pos)
        out = []
        i = bisect.bisect_right(self.u_starts, pos) - 1
        while n > 0 and i < len(self.blocks):
            usize, csize, flags = self.blocks[i]
            off = pos - self.u_starts[i]
            take = min(n, usize - off)
            if flags & 0x3F == 0:  # stored: read straight from the file, no cache
                self.fh.seek(self.c_starts[i] + off)
                out.append(self.fh.read(take))
            else:
                out.append(self._block(i)[off:off + take])
            pos += take
            n -= take
            i += 1
        return b"".join(out)

    def close(self) -> None:
        self.cache.clear()
        self.cached = 0
        try:
            self.fh.close()
        except OSError:
            pass


class SliceIO(io.RawIOBase):
    """A seekable read-only window ``[offset, offset+size)`` over a :class:`BlocksIO`."""

    def __init__(self, blocks: BlocksIO, offset: int, size: int, name: str = ""):
        super().__init__()
        self.blocks, self.offset, self.size, self.pos, self.name = blocks, offset, size, 0, name

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = max(0, base + pos)
        return self.pos

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        data = self.blocks.read_at(self.offset + self.pos, n)
        self.pos += len(data)
        return data

    def readall(self) -> bytes:
        return self.read(-1)


class LazyCabs(dict):
    """``Environment.cabs`` that parses a bundle's serialized CAB the first time someone asks for it."""

    def __init__(self, *args):
        super().__init__(*args)
        self.pending: dict[str, tuple] = {}

    def get(self, key, default=None):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        spec = self.pending.get(key)
        if spec is None:
            return default
        bundle, node_name = spec
        return parse_node(bundle, node_name)


def _simplify(name: str) -> str:
    from UnityPy.environment import simplify_name

    return simplify_name(name)


def _node_reader(bundle, node_name: str):
    from UnityPy.streams import EndianBinaryReader

    off, size, _flags = bundle._lazy_nodes[node_name]
    raw = io.BufferedReader(SliceIO(bundle._lazy_blocks, off, size, node_name), buffer_size=1 << 16)
    return EndianBinaryReader(raw, offset=0)


def parse_node(bundle, node_name: str):
    """Parse (once) and register one node of a lazy bundle; returns the SerializedFile or reader."""
    from UnityPy.helpers import ImportHelper

    if node_name in bundle.files:
        return bundle.files[node_name]
    f = ImportHelper.parse_file(_node_reader(bundle, node_name), bundle, node_name, is_dependency=False)
    f.flags = bundle._lazy_nodes[node_name][2]
    bundle.files[node_name] = f
    env = bundle.environment
    if env is not None:
        dict.__setitem__(env.cabs, _simplify(node_name), f)
    return f


def release_node(bundle, node_name: str) -> None:
    """Forget a parsed node (its object table is freed); it is parsed again if asked later."""
    f = bundle.files.pop(node_name, None)
    env = bundle.environment
    if env is not None:
        env.cabs.pop(_simplify(node_name), None)
    if f is not None and hasattr(f, "objects"):
        f.objects = {}


def is_resource_node(node_name: str) -> bool:
    return node_name.lower().endswith((".ress", ".resource"))


def load_bundle(env, path: str | Path, name: str | None = None, *, cache_bytes: int = CACHE_BYTES):
    """Open a UnityFS bundle lazily into a UnityPy ``Environment``: resource nodes become readers,
    serialized CABs are parsed on first use (``env.cabs`` is a :class:`LazyCabs`). Raises
    :class:`BundleTooLarge` when a single block cannot fit in RAM."""
    from UnityPy.files import BundleFile
    from UnityPy.files.File import File

    header = read_header(path)
    blocks = BlocksIO(path, header, cache_bytes=cache_bytes)
    bundle = BundleFile.__new__(BundleFile)
    File.__init__(bundle, parent=env, name=name or Path(path).name)
    bundle.signature, bundle.version = header.signature, header.version
    bundle.version_player, bundle.version_engine = header.version_player, header.version_engine
    bundle.decryptor = None
    bundle._lazy_blocks = blocks
    bundle._lazy_nodes = {n: (off, size, flags) for off, size, flags, n in header.nodes}
    if not isinstance(env.cabs, LazyCabs):
        env.cabs = LazyCabs(env.cabs)
    for _off, _size, flags, node_name in header.nodes:
        if is_resource_node(node_name):
            reader = _node_reader(bundle, node_name)
            reader.flags = flags
            bundle.files[node_name] = reader
            dict.__setitem__(env.cabs, _simplify(node_name), reader)
        else:
            env.cabs.pending[_simplify(node_name)] = (bundle, node_name)
    env.files[bundle.name] = bundle
    return bundle
