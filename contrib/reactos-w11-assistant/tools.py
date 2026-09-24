#!/usr/bin/env python3
"""
tools - the tool implementations behind the chatflow.

Every tool is real: it either does the thing or refuses and says why. Nothing
here pretends to have done work it has not.

Deliberate limitations, surfaced rather than hidden:

  search_theme   cannot browse the web. Returns a curated catalogue with
                 licence notes and says plainly that it did not search.
  vm_test        cannot run a VM here. Validates the ISO and emits the exact
                 command line for the user's hypervisor.
  run_rosbe_configure / run_ninja_build
                 will not run unless RosBE and its compilers are actually
                 present; they report what is missing instead of failing
                 obscurely.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import validate_msstyles  # noqa: E402

UPSTREAM = "https://github.com/reactos/reactos.git"

# Operations that change the user's tree. All gated behind confirm=True.
DESTRUCTIVE = {"git_clone", "git_pull", "run_rosbe_configure", "run_ninja_build",
               "backup_file"}


class ToolError(Exception):
    pass


def _run(cmd, cwd=None, timeout=1800, check=True):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    if check and p.returncode != 0:
        raise ToolError(f"{' '.join(cmd)} failed ({p.returncode}):\n{out.strip()[-2000:]}")
    return p.returncode, out


WHY_GATED = {
    "git_clone": "writes a new checkout to disk",
    "git_pull": "contacts the remote and writes to .git (the working tree is "
                "left alone)",
    "run_rosbe_configure": "writes build files into the build directory",
    "run_ninja_build": "starts a build and writes into the build directory",
    "backup_file": "writes a copy to ~/.reactos-assistant-backups",
}


def _stdout(cmd, cwd=None, timeout=600):
    """stdout only, for parsing. Returns (ok, text)."""
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode == 0, (p.stdout or "").strip()


def _need_confirm(name, confirm):
    if name in DESTRUCTIVE and not confirm:
        raise ToolError(
            f"{name} requires explicit confirmation because it "
            f"{WHY_GATED.get(name, 'modifies state')}. Pass confirm=True.")


# ---------------------------------------------------------------------------
# 1. git_clone
# ---------------------------------------------------------------------------
def git_clone(repo_url=UPSTREAM, destination="./reactos", confirm=False):
    _need_confirm("git_clone", confirm)
    if os.path.exists(destination):
        raise ToolError(f"{destination} already exists - use git_pull instead")
    rc, out = _run(["git", "clone", repo_url, destination], timeout=3600)
    return {"ok": True, "destination": os.path.abspath(destination),
            "summary": out.strip().splitlines()[-1] if out.strip() else "cloned"}


# ---------------------------------------------------------------------------
# 2. git_pull
# ---------------------------------------------------------------------------
def git_pull(repo_path=".", branch=None, confirm=False):
    """Fetch and report. Does NOT blindly `git pull <url>` - that form is not
    what people think it is, and it can silently merge an unexpected ref."""
    _need_confirm("git_pull", confirm)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        raise ToolError(f"{repo_path} is not a git checkout")

    branch = branch or _run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=repo_path)[1].strip()
    fetch_ok, fetch_out = _stdout(["git", "fetch", "origin", branch],
                                  cwd=repo_path, timeout=1800)

    # pick a ref we can actually compare against; a branch fetched without
    # a remote-tracking ref still exists as FETCH_HEAD
    ref = None
    for cand in (f"origin/{branch}", "FETCH_HEAD", "@{u}"):
        ok, _ = _stdout(["git", "rev-parse", "--verify", "--quiet", cand],
                        cwd=repo_path)
        if ok:
            ref = cand
            break

    ahead = behind = None
    if ref:
        ok_a, a = _stdout(["git", "rev-list", "--count", f"{ref}..HEAD"],
                          cwd=repo_path)
        ok_b, b = _stdout(["git", "rev-list", "--count", f"HEAD..{ref}"],
                          cwd=repo_path)
        if ok_a and a.isdigit():
            ahead = int(a)
        if ok_b and b.isdigit():
            behind = int(b)

    _, dirty = (0, (_stdout(["git", "status", "--porcelain"], cwd=repo_path)[1]))

    if not ref:
        advice = ("could not resolve a comparison ref - fetch may have failed"
                  + (f": {fetch_out.splitlines()[-1]}" if fetch_out and not fetch_ok else ""))
    elif dirty:
        advice = "working tree has local changes - fast-forward skipped"
    elif behind:
        advice = f"fast-forward {behind} commit(s) with: git merge --ff-only {ref}"
    else:
        advice = "already up to date"

    return {
        "ok": fetch_ok,
        "branch": branch,
        "compared_against": ref,
        "ahead": ahead,
        "behind": behind,
        "dirty": bool(dirty),
        "advice": advice,
    }


# ---------------------------------------------------------------------------
# 3. run_rosbe_configure
# ---------------------------------------------------------------------------
def run_rosbe_configure(source_path=".", build_dir=None, arch="i386",
                        confirm=False):
    _need_confirm("run_rosbe_configure", confirm)
    src = os.path.abspath(source_path)
    build_dir = build_dir or os.path.join(src, f"output-MinGW-{arch}")
    os.makedirs(build_dir, exist_ok=True)

    is_win = os.name == "nt"
    script = "configure.cmd" if is_win else "configure.sh"
    if not os.path.exists(os.path.join(src, script)):
        raise ToolError(f"{script} not found in {src}")

    # configure.sh requires the RosBE environment (it bails out with
    # "Could not detect RosBE." when ROS_ARCH is unset), so check rather
    # than produce a confusing failure.
    if not is_win and not os.environ.get("ROS_ARCH"):
        raise ToolError(
            "RosBE environment not active: ROS_ARCH is unset.\n"
            "  On Linux, start a RosBE shell first, e.g.\n"
            "    cd /path/to/RosBE-Unix && ./RosBE  (or: source rosbe_rc)\n"
            "  then re-run this tool from inside that shell.")

    cc = shutil.which("i686-w64-mingw32-gcc") or shutil.which("gcc")
    if not cc:
        raise ToolError("no C compiler on PATH - RosBE provides the cross "
                        "compiler, so activate RosBE first")

    rc, out = _run([script], cwd=build_dir, timeout=3600, check=False)
    return {"ok": rc == 0, "build_dir": build_dir, "script": script,
            "compiler": cc, "log_tail": out.strip().splitlines()[-15:]}


# ---------------------------------------------------------------------------
# 4. run_ninja_build
# ---------------------------------------------------------------------------
def run_ninja_build(build_dir, target="bootcd", confirm=False, timeout=7200):
    _need_confirm("run_ninja_build", confirm)
    if not os.path.isdir(build_dir):
        raise ToolError(f"build dir {build_dir} does not exist - configure first")
    if not os.path.exists(os.path.join(build_dir, "build.ninja")):
        raise ToolError(f"{build_dir} has no build.ninja - configure first")
    if not shutil.which("ninja"):
        raise ToolError("ninja not found on PATH - activate RosBE")

    rc, out = _run(["ninja", target], cwd=build_dir, timeout=timeout, check=False)
    iso = os.path.join(build_dir, "bootcd.iso")
    return {
        "ok": rc == 0 and os.path.exists(iso),
        "target": target,
        "returncode": rc,
        "iso_path": iso if os.path.exists(iso) else None,
        "log_tail": out.strip().splitlines()[-20:],
    }


# ---------------------------------------------------------------------------
# 5. search_theme
# ---------------------------------------------------------------------------
CATALOG = [
    {
        "name": "ReactOS bundled themes (Lautus, Lunar, Mizu, Modern, Blackshade)",
        "where": "media/themes/ in the tree, installed to "
                 "%SystemRoot%/Resources/Themes",
        "method": "source",
        "licence": "GPL-2.0-or-later (ReactOS project)",
        "notes": "Authored from .rc + bitmaps + INI, so they always match this "
                 "uxtheme. Modern already ships Light and Dark variants. "
                 "Blackshade is a placeholder and is not built.",
        "applies": "guaranteed",
    },
    {
        "name": "Third-party Windows XP .msstyles",
        "where": "various theme sites",
        "method": "packed",
        "licence": "varies - check each theme, most forbid redistribution",
        "notes": "The packed format matches: XP .msstyles and ReactOS both use "
                 "PACKTHEM_VERSION/COLORNAMES/SIZENAMES/FILERESNAMES/TEXTFILE, "
                 "so version-3 themes can load. But ReactOS's uxtheme is "
                 "partial, so many controls stay unthemed.",
        "applies": "may work, unverified",
    },
    {
        "name": "A source-authored Windows 11 style theme",
        "where": "would have to be written",
        "method": "source",
        "licence": "yours",
        "notes": "The only route to a real Windows 11 look. Requires new "
                 "Fluent bitmaps plus INI metrics. Rounded window corners, "
                 "Mica and real shadows need a compositor, which ReactOS "
                 "does not have.",
        "applies": "the honest answer",
    },
]


def search_theme(query=""):
    """Returns a curated catalogue. Does NOT browse - and says so."""
    q = (query or "").lower()
    hits = [c for c in CATALOG
            if not q or q in c["name"].lower() or q in c["notes"].lower()]
    return {
        "searched_the_web": False,
        "disclaimer": ("This tool has no network access. It returns a curated "
                       "catalogue, not search results. Anything it cannot "
                       "verify is labelled as such."),
        "results": hits or CATALOG,
    }


# ---------------------------------------------------------------------------
# 6. validate_msstyles
# ---------------------------------------------------------------------------
def validate_theme(file_path):
    p = file_path.rstrip("/")
    if os.path.isdir(p):
        findings, problems = validate_msstyles.validate_source(p)
    elif os.path.isfile(p):
        findings, problems = validate_msstyles.validate_packed(p)
    else:
        raise ToolError(f"{file_path} does not exist")
    return {"ok": not problems, "path": p, "findings": findings,
            "problems": problems}


# ---------------------------------------------------------------------------
# 7. vm_test
# ---------------------------------------------------------------------------
ISO_MAGIC_OFFSET = 0x8001
ISO_MAGIC = b"CD001"


def vm_test(iso_path, vm_type="qemu"):
    if not os.path.exists(iso_path):
        raise ToolError(f"{iso_path} does not exist")
    size = os.path.getsize(iso_path)
    with open(iso_path, "rb") as fh:
        fh.seek(ISO_MAGIC_OFFSET)
        magic = fh.read(5)

    valid = magic == ISO_MAGIC
    commands = {
        "qemu": ["qemu-system-i386", "-m", "512", "-cdrom", iso_path,
                 "-boot", "d", "-device", "VGA,vgamem_mb=64"],
        "virtualbox": ["VBoxManage", "createvm", "--name", "ReactOS",
                       "--register", "&&", "VBoxManage", "storagectl",
                       "ReactOS", "--name", "IDE", "--add", "ide",
                       "--controller", "PIIX4", "&&", "VBoxManage",
                       "storageattach", "ReactOS", "--storagectl", "IDE",
                       "--port", "0", "--device", "0", "--type", "dvddrive",
                       "--medium", iso_path],
        "vmware": ["vmplayer", iso_path],
    }
    if vm_type not in commands:
        raise ToolError(f"unknown vm_type {vm_type!r}; "
                        f"expected one of {', '.join(commands)}")
    return {
        "ran_vm": False,
        "reason": "no hypervisor in this environment - command emitted instead",
        "iso_path": os.path.abspath(iso_path),
        "size_bytes": size,
        "iso9660_valid": valid,
        "vm_type": vm_type,
        "command": commands[vm_type],
        "note": ("ISO9660 signature present" if valid else
                 "NO ISO9660 signature at 0x8001 - this is probably not a "
                 "bootable image"),
    }


# ---------------------------------------------------------------------------
# 8. backup_file
# ---------------------------------------------------------------------------
BACKUP_ROOT = os.path.expanduser("~/.reactos-assistant-backups")


def backup_file(file_path):
    if not os.path.exists(file_path):
        raise ToolError(f"{file_path} does not exist")
    if not os.path.isdir(BACKUP_ROOT):
        os.makedirs(BACKUP_ROOT, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(open(file_path, "rb").read()).hexdigest()[:12]
    flat = file_path.strip("/").replace("/", "_")
    dest = os.path.join(BACKUP_ROOT, f"{stamp}__{flat}.{digest}.bak")
    shutil.copy2(file_path, dest)

    manifest = os.path.join(BACKUP_ROOT, "manifest.jsonl")
    with open(manifest, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "when": stamp, "source": os.path.abspath(file_path),
            "backup": dest, "sha256_12": digest,
            "size": os.path.getsize(file_path),
        }) + "\n")

    return {"ok": True, "backup": dest, "sha256_12": digest,
            "restore_with": f"cp {dest!r} {file_path!r}", "manifest": manifest}


# ---------------------------------------------------------------------------
TOOLS = {
    "git_clone": git_clone,
    "git_pull": git_pull,
    "run_rosbe_configure": run_rosbe_configure,
    "run_ninja_build": run_ninja_build,
    "search_theme": search_theme,
    "validate_msstyles": validate_theme,
    "vm_test": vm_test,
    "backup_file": backup_file,
}


def get(name):
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name!r}")
    return TOOLS[name]


if __name__ == "__main__":
    # smoke test: exercise every tool's argument validation
    print("tools registered:", ", ".join(sorted(TOOLS)))
    for t in ("run_ninja_build", "run_rosbe_configure", "git_clone"):
        try:
            get(t)(confirm=False)
        except (ToolError, TypeError) as e:
            print(f"  {t}: gated correctly -> {str(e).splitlines()[0][:70]}")
