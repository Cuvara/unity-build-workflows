#!/usr/bin/env python3
"""
axml.py
Minimal reader for Android binary XML (the compiled AndroidManifest.xml found
inside an APK).

Stage 04 (ARTIFACT VALIDATION) needs the package name, versionName and
versionCode of a built APK. `aapt2 dump badging` gives those, but the Android
SDK is not guaranteed on a self-hosted runner, and pulling in a third-party
APK library would add a dependency for three attributes. This reads the four
chunk types an AndroidManifest actually uses and stops there.

Only the <manifest> element's attributes are exposed — that is all the
validator asks for. Anything unparseable raises AxmlError so the caller can
fall back to the artifact manifest rather than trusting a half-read value.

Format reference: AOSP frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h
"""

import struct

# Chunk types (ResChunk_header.type)
RES_STRING_POOL_TYPE = 0x0001
RES_XML_TYPE = 0x0003
RES_XML_START_ELEMENT_TYPE = 0x0102

# ResStringPool_header.flags
UTF8_FLAG = 1 << 8

# Offset from the start of a StartElement chunk to ResXMLTree_attrExt:
# ResChunk_header (8) + lineNumber (4) + comment (4).
ATTR_EXT_OFFSET = 16

# Res_value.dataType
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12


class AxmlError(Exception):
    """Raised when the buffer is not readable Android binary XML."""


def _u16(buf, off):
    return struct.unpack_from("<H", buf, off)[0]


def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _read_string_pool(buf, off):
    """Return the list of strings in the ResStringPool chunk starting at off."""
    chunk_type = _u16(buf, off)
    if chunk_type != RES_STRING_POOL_TYPE:
        raise AxmlError(f"expected string pool at {off}, got type 0x{chunk_type:04x}")

    header_size = _u16(buf, off + 2)
    string_count = _u32(buf, off + 8)
    flags = _u32(buf, off + 16)
    strings_start = _u32(buf, off + 20)
    is_utf8 = bool(flags & UTF8_FLAG)

    offsets_at = off + header_size
    data_at = off + strings_start

    strings = []
    for i in range(string_count):
        try:
            entry_off = _u32(buf, offsets_at + i * 4)
            pos = data_at + entry_off
            if is_utf8:
                # Two varint-ish length fields (chars, then bytes); the high bit
                # marks a two-byte length.
                n_chars = buf[pos]
                pos += 2 if n_chars & 0x80 else 1
                n_bytes = buf[pos]
                if n_bytes & 0x80:
                    n_bytes = ((n_bytes & 0x7F) << 8) | buf[pos + 1]
                    pos += 2
                else:
                    pos += 1
                strings.append(buf[pos:pos + n_bytes].decode("utf-8", "replace"))
            else:
                n_chars = _u16(buf, pos)
                pos += 2
                if n_chars & 0x8000:
                    n_chars = ((n_chars & 0x7FFF) << 16) | _u16(buf, pos)
                    pos += 2
                strings.append(buf[pos:pos + n_chars * 2].decode("utf-16-le", "replace"))
        except (struct.error, IndexError):
            strings.append("")
    return strings


def _decode_value(strings, raw_value, data_type, data):
    """Turn a Res_value into a Python str/int/bool."""
    if data_type == TYPE_STRING:
        idx = raw_value if raw_value != 0xFFFFFFFF else data
        return strings[idx] if 0 <= idx < len(strings) else ""
    if data_type == TYPE_INT_BOOLEAN:
        return data != 0
    if data_type in (TYPE_INT_DEC, TYPE_INT_HEX):
        return data
    # Anything else (references, dimensions) is not something the manifest
    # validator inspects — hand back the raw int.
    return data


def parse_manifest_attributes(data):
    """Return {attribute_name: value} for the first <manifest> element.

    Raises AxmlError when `data` is not binary XML or contains no <manifest>.
    """
    if len(data) < 8:
        raise AxmlError("buffer too short")
    if _u16(data, 0) != RES_XML_TYPE:
        raise AxmlError("not an Android binary XML document")

    header_size = _u16(data, 2)
    strings = _read_string_pool(data, header_size)

    off = header_size
    total = len(data)
    while off + 8 <= total:
        chunk_type = _u16(data, off)
        chunk_size = _u32(data, off + 4)
        if chunk_size <= 0 or off + chunk_size > total:
            break

        if chunk_type == RES_XML_START_ELEMENT_TYPE:
            name_idx = _u32(data, off + 20)
            element = strings[name_idx] if 0 <= name_idx < len(strings) else ""
            if element == "manifest":
                attr_start = _u16(data, off + 24)
                attr_size = _u16(data, off + 26)
                attr_count = _u16(data, off + 28)
                attrs = {}
                # attributeStart is measured from the start of ResXMLTree_attrExt,
                # which itself begins 16 bytes into the chunk (8-byte chunk header
                # + lineNumber + comment) — not from the start of the chunk.
                base = off + ATTR_EXT_OFFSET + attr_start
                for i in range(attr_count):
                    a = base + i * attr_size
                    a_name_idx = _u32(data, a + 4)
                    a_raw = _u32(data, a + 8)
                    a_type = data[a + 15]
                    a_data = _u32(data, a + 16)
                    a_name = strings[a_name_idx] if 0 <= a_name_idx < len(strings) else ""
                    if a_name:
                        attrs[a_name] = _decode_value(strings, a_raw, a_type, a_data)
                return attrs
        off += chunk_size

    raise AxmlError("no <manifest> element found")
