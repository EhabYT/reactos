#!/usr/bin/env python3
"""
validate_msstyles - check whether a ReactOS/XP theme is loadable.

Two modes:

  source dir   a theme *source* tree, i.e. media/themes/<Name>/<name>.msstyles/
               containing a .rc plus textfiles/ and bitmaps/
  packed file  a built .msstyles (a PE module). Its resource directory is
               parsed directly, because that is what uxtheme reads.

Why this exists: ReactOS's uxtheme loads a theme with LoadLibraryExW(...,
LOAD_LIBRARY_AS_DATAFILE) and then looks for specific *named resource types*
(dll/win32/uxtheme/msstyles.c). If any are missing it silently falls back to
the classic look, which is the usual cause of "my theme does nothing".

Required, per msstyles.c:
    PACKTHEM_VERSION   WORD, must be 3 (MSSTYLES_VERSION 0x0003)
    COLORNAMES         WCHAR list, names of the colour schemes
    SIZENAMES          WCHAR list, names of the size schemes
    FILERESNAMES       WCHAR list, the *_INI TEXTFILE names, in size order
  plus one TEXTFILE resource per entry in FILERESNAMES.
"""

import argparse
import os
import re
import struct
import sys

MSSTYLES_VERSION = 0x0003
REQUIRED_TYPES = ("PACKTHEM_VERSION", "COLORNAMES", "SIZENAMES", "FILERESNAMES")


# ---------------------------------------------------------------------------
# packed (.msstyles PE) mode
# ---------------------------------------------------------------------------
class PEFormatError(Exception):
    pass


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def pe_resource_types(data):
    """Return (set_of_named_types, set_of_numeric_types) or raise PEFormatError."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise PEFormatError("not a PE file (no MZ signature)")

    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise PEFormatError("no PE signature at e_lfanew")

    coff = e_lfanew + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20

    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:
        dd_off = opt + 96
    elif magic == 0x20B:
        dd_off = opt + 112
    else:
        raise PEFormatError(f"unknown optional header magic 0x{magic:04x}")

    res_rva, res_size = struct.unpack_from("<II", data, dd_off + 2 * 8)
    if res_rva == 0:
        return set(), set(), "no resource directory"

    # section table -> RVA translation
    secs = []
    st = opt + opt_size
    for i in range(num_sections):
        off = st + i * 40
        va, raw_size, raw_ptr = struct.unpack_from("<III", data, off + 12)
        secs.append((va, raw_size, raw_ptr))

    def rva_to_off(rva):
        for va, size, ptr in secs:
            if va <= rva < va + max(size, 1):
                return ptr + (rva - va)
        raise PEFormatError(f"RVA 0x{rva:x} not in any section")

    base = rva_to_off(res_rva)
    named, numeric = set(), set()

    def parse_dir(off, level):
        if level > 2:
            return
        n_named, n_id = struct.unpack_from("<HH", data, off + 12)
        total = n_named + n_id
        for i in range(total):
            e = off + 16 + i * 8
            name, offset = struct.unpack_from("<II", data, e)
            if level == 0:
                if name & 0x80000000:
                    slen = struct.unpack_from("<H", data, base + (name & 0x7FFFFFFF))[0]
                    s = data[base + (name & 0x7FFFFFFF) + 2:
                             base + (name & 0x7FFFFFFF) + 2 + slen * 2]
                    named.add(s.decode("utf-16-le", "replace"))
                else:
                    numeric.add(name)
            if offset & 0x80000000:
                parse_dir(base + (offset & 0x7FFFFFFF), level + 1)

    parse_dir(base, 0)
    return named, numeric, None


def validate_packed(path):
    findings, problems = [], []
    try:
        data = _read(path)
    except OSError as e:
        return [f"cannot read: {e}"], ["unreadable"]

    findings.append(f"file size: {len(data)} bytes")
    try:
        named, numeric, note = pe_resource_types(data)
    except PEFormatError as e:
        return findings + [f"PE parse failed: {e}"], ["not a loadable theme module"]

    if note:
        problems.append("no resource directory - uxtheme will not find any theme data")
        return findings, problems

    findings.append(f"named resource types: {', '.join(sorted(named)) or '(none)'}")
    if numeric:
        findings.append(f"numeric resource types: "
                        f"{', '.join(str(n) for n in sorted(numeric))}")

    for t in REQUIRED_TYPES:
        if t in named:
            findings.append(f"  [ok]   {t}")
        else:
            problems.append(f"missing required resource type: {t}")
            findings.append(f"  [FAIL] {t}")

    if "TEXTFILE" in named:
        findings.append("  [ok]   TEXTFILE")
    else:
        problems.append("no TEXTFILE resources - the theme has no section data")

    return findings, problems


# ---------------------------------------------------------------------------
# source-directory mode
# ---------------------------------------------------------------------------
def find_rc(theme_dir):
    rcs = [f for f in os.listdir(theme_dir) if f.endswith(".rc")]
    return os.path.join(theme_dir, rcs[0]) if rcs else None


def validate_source(theme_dir):
    findings, problems = [], []
    rc = find_rc(theme_dir)
    if not rc:
        return [f"no .rc found in {theme_dir}"], ["not a theme source directory"]

    findings.append(f"resource script: {os.path.basename(rc)}")
    text = open(rc, encoding="utf-8", errors="replace").read()

    for t in REQUIRED_TYPES:
        if re.search(rf"^\s*\d+\s+{t}\b", text, re.M | re.I):
            findings.append(f"  [ok]   {t} declared")
        else:
            problems.append(f"{t} not declared in the .rc")
            findings.append(f"  [FAIL] {t}")

    m = re.search(r"^\s*\d+\s+PACKTHEM_VERSION\s*\{([^}]*)\}", text, re.M | re.I)
    if m:
        raw = m.group(1)
        vals = [int(x, 16) if x.strip().lower().startswith("0x") else int(x)
                for x in re.findall(r"0x[0-9a-fA-F]+|\d+", raw)]
        if vals and vals[0] != MSSTYLES_VERSION:
            problems.append(
                f"PACKTHEM_VERSION is {vals[0]}, uxtheme requires "
                f"{MSSTYLES_VERSION} (MSSTYLES_VERSION)")
            findings.append(f"  [FAIL] version {vals[0]} != {MSSTYLES_VERSION}")
        elif vals:
            findings.append(f"  [ok]   version {vals[0]}")

    # FILERESNAMES -> *_INI resources, and the TEXTFILEs they must resolve to
    names = []
    m = re.search(r"^\s*\d+\s+FILERESNAMES\s*\{([^}]*)\}", text, re.M | re.I)
    if m:
        # the resource body is a NUL-separated, double-NUL-terminated WCHAR list
        raw = "".join(re.findall(r'L"([^"]*)"', m.group(1)))
        names = [n for n in raw.split("\\0") if n]
        if names:
            findings.append(f"FILERESNAMES: {', '.join(names)}")
    for n in names:
        if re.search(rf"^\s*{re.escape(n)}\s+TEXTFILE\b", text, re.M | re.I):
            findings.append(f"  [ok]   TEXTFILE {n} present")
        else:
            problems.append(f"FILERESNAMES lists {n} but no such TEXTFILE resource")
            findings.append(f"  [FAIL] TEXTFILE {n}")

    # textfiles/ referenced by the .rc should exist on disk (unbuilt source)
    for m in re.finditer(r'TEXTFILE\s+"([^"]+)"', text, re.I):
        rel = m.group(1)
        if rel.startswith("textfiles/") and "_utf16" in rel:
            continue          # generated by utf16le_convert at build time
        if not os.path.exists(os.path.join(theme_dir, rel)):
            problems.append(f"TEXTFILE source missing on disk: {rel}")

    return findings, problems


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="theme source directory or packed .msstyles file")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    path = args.path.rstrip("/")
    if os.path.isdir(path):
        findings, problems = validate_source(path)
        kind = "source directory"
    elif os.path.isfile(path):
        findings, problems = validate_packed(path)
        kind = "packed .msstyles"
    else:
        print(f"not found: {path}")
        return 2

    print(f"== {path}")
    print(f"   mode: {kind}")
    if not args.quiet:
        for f in findings:
            print(f"   {f}")
    print()
    if problems:
        print(f"   RESULT: NOT LOADABLE AS-IS ({len(problems)} problem(s))")
        for p in problems:
            print(f"     - {p}")
        return 1
    print("   RESULT: all required resources present - uxtheme should load it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
