#!/usr/bin/env python3
"""Tools for syncing ReactOS wine components to a newer Wine.

    sync.py status                regenerate /home/user/wine-sync-status.md
    sync.py list [N]              smallest actionable components
    sync.py brief <component>...  what upstream changed, with lineage
    sync.py diff <component>...   the same plus the upstream diff
    sync.py merge <wine> <ros>    three-way merge one file (--apply to write)
    sync.py verify <wine> <ros>   check a merged file
                                  (--intentional "reason" to allow skips)

Lineage is how much of our local file is unchanged from the Wine-10.0
baseline. A low number means our copy is a different implementation, and a
three-way merge against Wine would mix two programs together, so those files
are never merge candidates however small the upstream delta looks.

Requires two shallow clones:

    git clone --depth 1 -b wine-10.0  https://github.com/wine-mirror/wine.git /tmp/wine-10.0
    git clone --depth 1 -b wine-11.18 https://github.com/wine-mirror/wine.git /tmp/wine-11.18

Both are volatile: recreate them after a sandbox reset.
"""
import difflib
import os
import re
import subprocess
import sys

ROS = "/home/user/reactos"
W10 = "/tmp/wine-10.0"
W11 = "/tmp/wine-11.18"
LEDGER = os.path.join(ROS, "media/doc/WINESYNC.txt")
REPORT = "/home/user/wine-sync-status.md"
THRESHOLD = 70.0  # % of local lines shared with the wine baseline


# ---------------------------------------------------------------- mapping

def wine_dirs_for(local_path):
    """Candidate Wine directories for a local path like dll/win32/mpr.

    Take the most specific directory that exists, not a union: ReactOS does
    not mirror Wine's layout everywhere. dll/win32/winmm/midimap is Wine's
    top-level dlls/midimap, and modules/rostests/winetests/<mod> holds
    Wine's dlls/<mod>/tests. Falling back to the parent module made those
    entries inherit the whole of winmm's delta.
    """
    p = local_path.strip("/")
    parts = p.split("/")
    cands = []
    if len(parts) >= 3 and parts[0] == "dll" and parts[1] == "win32":
        mod, rest = parts[2], parts[3:]
        if not rest:
            cands.append("dlls/" + mod)
        else:
            # exact nesting, then Wine's flat layout (dlls/midimap), then a
            # ReactOS-only subdirectory such as lang/ that belongs to the
            # parent module
            cands += ["dlls/" + mod + "/" + "/".join(rest),
                      "dlls/" + "/".join(rest),
                      "dlls/" + mod]
    elif p.startswith("modules/rostests/winetests/"):
        cands.append("dlls/" + parts[3] + "/tests")
    elif p.startswith("dll/directx/wine/"):
        cands.append("dlls/" + p.split("/")[3])
    elif len(parts) >= 3 and parts[0] == "base" and parts[1] == "applications":
        cands.append("programs/" + "/".join(parts[2:]))
    elif p.startswith("base/system/"):
        cands.append("programs/" + p.split("/")[2])
    elif p.startswith("sdk/lib/"):
        cands.append("libs/" + p.split("/")[2])
    elif p.startswith("sdk/tools/"):
        cands.append("tools/" + p.split("/")[2])
    else:
        cands.append("dlls/" + parts[-1])
    named = [c for c in cands if os.path.isdir(os.path.join(W11, c))]
    if named:
        return named[:1]
    base = os.path.basename(p)
    for top in ("dlls", "programs", "libs", "tools"):
        for c in (f"{top}/{base}", f"{top}/{base.lower()}"):
            if os.path.isdir(os.path.join(W11, c)):
                return [c]
    return []


def rospath(wrel, local_dir=None):
    """Map a Wine file to our local path.

    Names differ in places: Wine's main.c is our <module>_main.c and Wine's
    async_reader.c is our reader.c. Try the obvious forms before giving up,
    so a renamed file is not mistaken for a new one.
    """
    parts = wrel.split("/")
    if parts[0] == "dlls":
        base_dir = os.path.join("dll/win32", parts[1])
        cand = os.path.join(base_dir, os.path.basename(wrel))
        if os.path.exists(os.path.join(ROS, cand)):
            return cand
        comp = parts[1]
        name = os.path.basename(wrel)
        root, ext = os.path.splitext(name)
        tries = [os.path.join(base_dir, comp + "_" + name)]
        if root.startswith("async_"):
            short = root[len("async_"):]
            tries += [os.path.join(base_dir, short + ext),
                      os.path.join(base_dir, comp + "_" + short + ext)]
        for t in tries:
            if os.path.exists(os.path.join(ROS, t)):
                return t
        return cand
    if parts[0] == "programs":
        c = os.path.join("base/applications", *parts[1:])
        if os.path.exists(os.path.join(ROS, c)):
            return c
        alt = os.path.join("base/system", parts[1], os.path.basename(wrel))
        return alt if os.path.exists(os.path.join(ROS, alt)) else c
    if parts[0] in ("libs", "tools"):
        return os.path.join("sdk/lib" if parts[0] == "libs" else "sdk/tools",
                            *parts[1:])
    return None


def find_local(name):
    for root, dirs, files in os.walk(ROS):
        if "/contrib" in root or "/.git" in root:
            continue
        if os.path.basename(root) == name:
            return os.path.relpath(root, ROS)
    return None


# ---------------------------------------------------------------- queries

def lineage(wrel, rrel):
    wp, rp = os.path.join(W10, wrel), os.path.join(ROS, rrel)
    if not os.path.exists(wp) or not os.path.exists(rp):
        return None
    a = open(wp, encoding="utf-8", errors="replace").read().splitlines()
    b = open(rp, encoding="utf-8", errors="replace").read().splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return 100.0 * sum(bl.size for bl in sm.get_matching_blocks()) / max(1, len(b))


def changed_files(wdir):
    r = subprocess.run(["diff", "-r", "-q", "--exclude=.git",
                        os.path.join(W10, wdir), os.path.join(W11, wdir)],
                       capture_output=True, text=True)
    out = {"C": [], "A": [], "D": []}
    for ln in (r.stdout + r.stderr).splitlines():
        if ln.startswith("Files ") and " differ" in ln:
            out["C"].append(ln.split(" ", 2)[1][len(W10) + 1:])
        elif ln.startswith("Only in "):
            head, _, name = ln.rpartition(": ")
            head = head[len("Only in "):]
            side = "A" if head.startswith(os.path.join(W11, wdir)) else "D"
            out[side].append(os.path.relpath(os.path.join(head, name), W10))
    return out


def parse_ledger():
    entries = []
    for ln in open(LEDGER, encoding="utf-8", errors="replace"):
        body, _, comment = ln.partition("#")
        body = body.strip()
        if not body:
            continue
        paths = [p.strip() for p in (body.split("=>") if "=>" in body
                                     else [body])]
        entries.append({"paths": paths, "comment": comment.strip()})
    return entries


def delta_for(local_dir):
    """[(status, wine_rel, ros_rel, lineage)] for one component."""
    wdirs = wine_dirs_for(local_dir)
    files = []
    for wd in wdirs:
        ch = changed_files(wd)
        for status, wrel in ([("C", f) for f in ch["C"]] +
                             [("A", f) for f in ch["A"]] +
                             [("D", f) for f in ch["D"]]):
            if "/tests/" in wrel or wrel.endswith("Makefile.in"):
                continue
            rrel = rospath(wrel, local_dir)
            exists = rrel and os.path.exists(os.path.join(ROS, rrel))
            if not exists:
                if status == "D":
                    continue  # deleted upstream and we never had it
                files.append((status, wrel, rrel, None))
                continue
            files.append((status, wrel, rrel, lineage(wrel, rrel)))
    return wdirs, files


# ---------------------------------------------------------------- classify

def classify():
    b = {"done": 0, "mergeable": [], "deferred": [], "otherimpl": [],
         "nodelta": [], "unmapped": [], "nocounterpart": [], "versiononly": [],
         "tests": []}
    for e in parse_ledger():
        local_dirs = [p for p in e["paths"] if os.path.isdir(os.path.join(ROS, p))]
        if not local_dirs:
            continue
        # Order matters. "Synced to Wine-11.18 (..., see below)" is a finished
        # component that carries a recorded deviation - six entries read that
        # way, for kept version resources, the WIN32_NO_STATUS removal and
        # imagehlp's spec signatures. Plain "(... - see below)" without a
        # release name is the deferred form. So test for the release first.
        if "Wine-11.18" in e["comment"]:
            b["done"] += 1
            continue
        if "see below" in e["comment"]:
            b["deferred"].append((e, local_dirs))
            continue
        if all(d.startswith("modules/rostests/winetests/") for d in local_dirs):
            b["tests"].append((e, local_dirs))
            continue
        rec = (e, local_dirs, [])
        for d in local_dirs:
            wdirs, files = delta_for(d)
            rec[2].append((d, wdirs, files))
        if not any(wdirs for _, wdirs, _ in rec[2]):
            b["unmapped"].append((e, local_dirs))
            continue
        allfiles = [f for _, _, fs in rec[2] for f in fs]
        if not allfiles:
            b["nodelta"].append(rec)
        elif all(f[0] == "D" for f in allfiles):
            b["versiononly"].append(rec)
        elif not any(f[3] is not None for f in allfiles):
            b["nocounterpart"].append(rec)
        elif any(f[3] is not None and f[3] >= THRESHOLD for f in allfiles):
            b["mergeable"].append(rec)
        else:
            b["otherimpl"].append(rec)
    return b


# ---------------------------------------------------------------- commands

def cmd_status(_):
    b = classify()
    L = ["# Wine sync status", "",
         "Backlog against Wine-11.18. A file counts as a candidate merge only if",
         "it is actually Wine's code: many files under a ledger entry are a ReactOS",
         "implementation, and merging a Wine baseline into one of those mixes two",
         "programs together.", "",
         f"Already at Wine-11.18: **{b['done']}**  ",
         f"Mergeable, real upstream delta: **{len(b['mergeable'])}**  ",
         f"Deferred, annotated in the ledger: **{len(b['deferred'])}**  ",
         f"Upstream only deleted a version resource: **{len(b['versiononly'])}**  ",
         f"Changed upstream but not Wine's code: **{len(b['otherimpl'])}**  ",
         f"Changed upstream, no local counterpart: **{len(b['nocounterpart'])}**  ",
         f"No upstream delta: **{len(b['nodelta'])}**  ",
         f"Upstream test suites only: **{len(b['tests'])}**  ",
         f"Not mapped to a Wine directory: **{len(b['unmapped'])}**", "",
         "## Mergeable", ""]
    for e, local_dirs, groups in sorted(b["mergeable"],
                                        key=lambda r: sum(len(f) for _, _, f in r[2])):
        n = sum(len(f) for _, _, f in groups)
        L.append(f"\n### `{', '.join(local_dirs)}` ({n} file(s))")
        if e["comment"]:
            L.append(f"\nLedger: `{e['comment']}`\n")
        L.append("\n| file | upstream | our copy is Wine's |")
        L.append("|---|---|---|")
        for _, _, files in groups:
            for status, wrel, rrel, pct in files:
                mark = {"C": "changed", "A": "added", "D": "deleted"}[status]
                pc = "n/a" if pct is None else f"{pct:.0f}%"
                L.append(f"| `{rrel}` | {mark} | {pc} |")

    if b["deferred"]:
        L.append("\n\n## Deferred\n")
        L.append("An upstream delta exists, but the local code is a different\n"
                 "implementation or the change is a new API rather than a merge.\n"
                 "The reason is in the notes for syncers and on the ledger entry.\n")
        for e, local_dirs in sorted(b["deferred"], key=lambda r: r[1][0]):
            L.append(f"- `{', '.join(local_dirs)}` - {e['comment']}")
        L.append("")

    L.append("\n\n## Upstream only deleted a version resource\n")
    L.append("Nothing to take: Wine moved version info into VER_PRODUCTVERSION in\n"
             "its Makefiles, which our CMake build has no equivalent for. Keep the\n"
             "local .rc.\n")
    for e, local_dirs, groups in sorted(b["versiononly"], key=lambda r: r[1][0]):
        names = ", ".join(f"`{f[2]}`" for _, _, fs in groups for f in fs)
        L.append(f"\n- `{', '.join(local_dirs)}`: {names}")

    L.append("\n\n## Changed upstream but not Wine's code\n")
    L.append("Do not merge.\n")
    for e, local_dirs, groups in sorted(b["otherimpl"], key=lambda r: r[1][0]):
        parts = []
        for _, _, fs in groups:
            for f in fs:
                pct = "no local counterpart" if f[3] is None else f"{f[3]:.0f}% Wine"
                parts.append(f"`{f[2]}` ({pct})")
        L.append(f"\n- `{', '.join(local_dirs)}`: " + ", ".join(parts))

    L.append("\n\n## Changed upstream, no local counterpart\n")
    L.append("Generated specs, resources we build another way: nothing to merge.\n")
    for e, local_dirs, groups in sorted(b["nocounterpart"], key=lambda r: r[1][0]):
        names = ", ".join(f"`{f[1]}`" for _, _, fs in groups for f in fs)
        L.append(f"\n- `{', '.join(local_dirs)}`: {names}")

    if b["tests"]:
        L.append("\n\n## Upstream test suites only\n")
        L.append("We keep our own copies under modules/rostests/winetests;\n"
                 "Wine's test deltas are not mergeable.\n")
        for e, local_dirs in sorted(b["tests"], key=lambda r: r[1][0]):
            L.append(f"\n- `{', '.join(local_dirs)}` - {e['comment'] or 'no comment'}")

    L.append("\n\n## No upstream delta\n")
    L.append(", ".join(f"`{e['paths'][0]}`" for e, *_ in
                       sorted(b["nodelta"], key=lambda r: r[0]["paths"][0])))
    L.append("\n")

    if b["unmapped"]:
        L.append("\n## Not mapped to a Wine directory\n")
        for e, local_dirs in b["unmapped"]:
            L.append(f"- `{', '.join(local_dirs)}` - {e['comment'] or 'no comment'}")
        L.append("\n")

    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    for k in ("done", "mergeable", "deferred", "versiononly", "otherimpl",
              "nocounterpart", "nodelta", "tests", "unmapped"):
        v = b[k] if isinstance(b[k], int) else len(b[k])
        print(f"{k:15s} {v}")
    print(f"report          {REPORT}")


def cmd_list(args):
    n = int(args[0]) if args else 20
    b = classify()
    rows = sorted(b["mergeable"], key=lambda r: sum(len(f) for _, _, f in r[2]))
    for e, local_dirs, groups in rows[:n]:
        names = ", ".join(f"{f[2]}"
                          + (f"({f[3]:.0f}%)" if f[3] is not None else "(new)")
                          for _, _, fs in groups for f in fs)
        cnt = sum(len(fs) for _, _, fs in groups)
        print(f"{cnt:3d}  {', '.join(local_dirs):42s} {names[:84]}")


def cmd_brief(args, with_diff=False):
    for comp in args:
        ld = find_local(comp)
        if not ld:
            print(f"{comp}: no local directory found")
            continue
        print(f"\n{'='*72}\n{comp}  ({ld})\n{'='*72}")
        wdirs, files = delta_for(ld)
        if not files:
            print(f"  no upstream delta in {wdirs or 'any mapped directory'}")
        for status, wrel, rrel, pct in files:
            if pct is None:
                print(f"  {wrel} -> no local counterpart ({rrel})")
            else:
                d = subprocess.run(["diff", "-u", os.path.join(W10, wrel),
                                    os.path.join(W11, wrel)],
                                   capture_output=True, text=True).stdout.splitlines()
                adds = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
                rems = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
                hun = sum(1 for l in d if l.startswith("@@"))
                print(f"  {rrel}  lineage {pct:.0f}%  [{hun}h +{adds} -{rems}]")
            if with_diff:
                d = subprocess.run(["diff", "-u", os.path.join(W10, wrel),
                                    os.path.join(W11, wrel)],
                                   capture_output=True, text=True).stdout
                print("\n".join("      " + l for l in d.splitlines()[:250]))


def cmd_merge(args):
    apply = "--apply" in sys.argv
    a = [x for x in args if not x.startswith("--")]
    if len(a) != 2:
        sys.exit("merge needs <wine_rel> <ros_rel>")
    wrel, rrel = a
    base, ours, theirs = (os.path.join(W10, wrel), os.path.join(ROS, rrel),
                          os.path.join(W11, wrel))
    for p in (base, ours, theirs):
        if not os.path.exists(p):
            sys.exit(f"missing: {p}")
    # NOTE: with -p, git merge-file writes to stdout only and does NOT modify
    # the first file, so the output has to be written explicitly. Getting this
    # wrong silently leaves the file unchanged while reporting success.
    r = subprocess.run(["git", "merge-file", "-p", "--diff3", ours, base, theirs],
                       capture_output=True, text=True)
    merged, conflicts = r.stdout, r.returncode
    if not merged.strip():
        sys.exit("merge produced no output - refusing to write")
    print(rrel)
    print(f"  ours {len(open(ours, encoding='utf-8', errors='replace').read().splitlines())} lines local")
    print("  CLEAN - all upstream changes applied over our fork" if not conflicts
          else f"  {conflicts} CONFLICT(S)")
    if apply:
        if conflicts:
            out = "/home/user/sync-conflict.txt"
            open(out, "w", encoding="utf-8").write(merged)
            print(f"  NOT applied; conflicted result in {out}")
            return 1
        open(ours, "w", encoding="utf-8").write(merged)
        print(f"  applied -> {rrel}")
    return 0


def cmd_verify(args):
    a = [x for x in args if not x.startswith("--")]
    intentional = None
    if "--intentional" in sys.argv:
        intentional = sys.argv[sys.argv.index("--intentional") + 1]
        a = [x for x in a if x != intentional]
    wrel, rrel = a

    def read(p):
        return open(p, encoding="utf-8", errors="replace").read()

    base, theirs, ours = read(os.path.join(W10, wrel)), read(os.path.join(W11, wrel)), read(os.path.join(ROS, rrel))
    before = subprocess.run(["git", "-C", ROS, "show", f"HEAD:{rrel}"],
                            capture_output=True, text=True).stdout
    mb = len(re.findall(r"__REACTOS__", before))
    ma = len(re.findall(r"__REACTOS__", ours))
    sm = difflib.SequenceMatcher(None, base.splitlines(), theirs.splitlines(),
                                 autojunk=False)
    added = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            added += [l.strip() for l in theirs.splitlines()[j1:j2] if l.strip()]
    present = set(l.strip() for l in ours.splitlines() if l.strip())
    missing = [l for l in added if l not in present]

    print(rrel)
    print(f"  __REACTOS__ markers: {mb} -> {ma}")
    if missing and intentional:
        print(f"  {len(missing)} upstream line(s) intentionally not taken: {intentional}")
        return 0
    print(f"  upstream new lines present: {len(added) - len(missing)}/{len(added)}")
    for m in missing[:8]:
        print(f"     MISSING: {m[:88]}")
    ok = mb == ma and not missing
    print("  OK" if ok else "  NEEDS REVIEW")
    return 0 if ok else 1


def main():
    if not os.path.isdir(W10) or not os.path.isdir(W11):
        sys.exit("wine clones missing - see the docstring")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "status":
        cmd_status(args)
    elif cmd == "list":
        cmd_list(args)
    elif cmd == "brief":
        cmd_brief(args)
    elif cmd == "diff":
        cmd_brief(args, with_diff=True)
    elif cmd == "merge":
        return cmd_merge(args)
    elif cmd == "verify":
        return cmd_verify(args)
    else:
        sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
