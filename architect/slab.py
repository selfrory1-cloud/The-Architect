"""TaleSpire slab (format v2) encoder / decoder.

Spec: https://github.com/Bouncyrock/DumbSlabStats/blob/master/format.md

Binary layout (little-endian), gzip-compressed then base64-encoded:
    u32 magic (0xD1CEFACE) | u16 version (2) | u16 layoutCount | u16 creatureCount (0)
    layoutCount * { uuid AssetKindId (.NET byte order) | u16 count | u16 reserved }
    per layout, count * u64:  | 5 unused | 5 rot | 18 z*100 | 18 y*100 | 18 x*100 |
    u16 0 (trailing creature padding, as written by TaleSpire)
"""
from __future__ import annotations

import base64
import gzip
import io
import struct
import uuid as uuidlib
from dataclasses import dataclass

MAGIC = 0xD1CEFACE
VERSION = 2
MAX_COMPRESSED_BYTES = 30720
_COORD_BITS = 18
_COORD_MAX = (1 << _COORD_BITS) - 1


@dataclass(frozen=True)
class SlabAsset:
    uuid: str
    x: float
    y: float
    z: float
    rot: int  # 0..23, 15 degree steps


class SlabError(ValueError):
    pass


def _pack(x: float, y: float, z: float, rot: int) -> int:
    sx, sy, sz = (int(round(v * 100)) for v in (x, y, z))
    for v in (sx, sy, sz):
        if not 0 <= v <= _COORD_MAX:
            raise SlabError(f"coordinate {v / 100} out of range (0..{_COORD_MAX / 100})")
    if not 0 <= rot < 24:
        raise SlabError(f"rotation {rot} out of range (0..23)")
    return sx | (sy << 18) | (sz << 36) | (rot << 54)


def _unpack(v: int) -> tuple[float, float, float, int]:
    return (
        (v & _COORD_MAX) / 100.0,
        ((v >> 18) & _COORD_MAX) / 100.0,
        ((v >> 36) & _COORD_MAX) / 100.0,
        (v >> 54) & 0x1F,
    )


def encode(assets: list[SlabAsset], normalize: bool = True) -> str:
    """Encode assets into a TaleSpire slab string.

    When ``normalize`` is set the build is shifted so its minimum corner sits at 0,
    since slab coordinates must be non-negative.
    """
    if not assets:
        raise SlabError("cannot encode an empty slab")
    if normalize:
        mx = min(a.x for a in assets)
        my = min(a.y for a in assets)
        mz = min(a.z for a in assets)
        assets = [SlabAsset(a.uuid, a.x - mx, a.y - my, a.z - mz, a.rot) for a in assets]

    groups: dict[str, list[SlabAsset]] = {}
    for a in assets:
        groups.setdefault(str(uuidlib.UUID(a.uuid)), []).append(a)
    if len(groups) > 0xFFFF or any(len(g) > 0xFFFF for g in groups.values()):
        raise SlabError("too many assets for a single slab")

    buf = io.BytesIO()
    buf.write(struct.pack("<IHHH", MAGIC, VERSION, len(groups), 0))
    for uid, items in groups.items():
        buf.write(uuidlib.UUID(uid).bytes_le)
        buf.write(struct.pack("<HH", len(items), 0))
    for items in groups.values():
        for a in items:
            buf.write(struct.pack("<Q", _pack(a.x, a.y, a.z, a.rot)))
    buf.write(struct.pack("<H", 0))

    compressed = gzip.compress(buf.getvalue(), compresslevel=9, mtime=0)
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise SlabError(
            f"slab is {len(compressed)} bytes compressed; TaleSpire limit is {MAX_COMPRESSED_BYTES}"
        )
    return base64.b64encode(compressed).decode("ascii")


def decode(code: str) -> list[SlabAsset]:
    """Decode a slab string (as copied from TaleSpire) into assets."""
    s = code.strip().strip("`").strip()
    try:
        data = gzip.decompress(base64.b64decode(s, validate=False))
    except Exception as exc:  # noqa: BLE001 - surface any decode failure uniformly
        raise SlabError(f"not a valid slab string: {exc}") from exc
    if len(data) < 10:
        raise SlabError("slab data too short")
    magic, version, layout_count, _creatures = struct.unpack_from("<IHHH", data, 0)
    if magic != MAGIC:
        raise SlabError("bad slab magic number")
    if version != VERSION:
        raise SlabError(f"unsupported slab version {version}")
    off = 10
    layouts = []
    for _ in range(layout_count):
        uid = str(uuidlib.UUID(bytes_le=data[off : off + 16]))
        (count,) = struct.unpack_from("<H", data, off + 16)
        layouts.append((uid, count))
        off += 20
    out: list[SlabAsset] = []
    for uid, count in layouts:
        for _ in range(count):
            (v,) = struct.unpack_from("<Q", data, off)
            off += 8
            x, y, z, rot = _unpack(v)
            out.append(SlabAsset(uid, x, y, z, rot))
    return out
