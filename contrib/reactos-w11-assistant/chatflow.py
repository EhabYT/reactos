#!/usr/bin/env python3
"""
chatflow - the ten-state assistant described in the prompt.

Runs as a CLI:

    python3 chatflow.py            interactive
    python3 chatflow.py --demo     replay the documented example flow
    python3 chatflow.py --state    dump the session state schema

The state machine is deliberately boring: one question per state, no skipping
the environment check, and no destructive tool call without the user typing
"yes". Tools that cannot run in the current environment say so instead of
pretending.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tools  # noqa: E402

ALPHA_WARNING = (
    "ReactOS is alpha software. It can corrupt partitions, lose data and fail "
    "to boot on real hardware. Do everything in a virtual machine and keep "
    "backups you can restore."
)

METHOD_TABLE = """| Method | Difficulty | Stability | Reversible? | Notes |
|--------|------------|-----------|-------------|-------|
| A. Third-party .msstyles | Easy | Low | Yes | format matches, but many controls stay unthemed |
| B. Source modification | Hard | Very low | Yes, with a rebuild | the only route to a real Windows 11 look |
| C. Hybrid (theme + icons + wallpaper + shell) | Medium | Medium | Mostly | shell replacements are the risky part |"""


class Session:
    """Mirrors the session-state schema in the prompt."""

    def __init__(self):
        self.s = {
            "session_id": "local",
            "host_os": None,
            "rosbe_installed": None,
            "git_installed": None,
            "vm_available": None,
            "disk_space_gb": 0,
            "goal": None,
            "repo_path": None,
            "build_dir": None,
            "iso_path": None,
            "ui_method": None,
            "theme_path": None,
            "errors": [],
            "last_state": "0",
        }

    def __getitem__(self, k):
        return self.s[k]

    def __setitem__(self, k, v):
        self.s[k] = v

    def set_state(self, n):
        self.s["last_state"] = str(n)

    def dump(self):
        return json.dumps(self.s, indent=2)


def warn(text):
    return f"> \u26a0\ufe0f WARNING: {text}"


def ask(prompt, default=None, choices=None):
    suffix = ""
    if choices:
        suffix = " [" + "/".join(choices) + "]"
    if default:
        suffix += f" (default: {default})"
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return raw or default


def confirm(prompt):
    return ask(f"{prompt} (type 'yes' to proceed)").lower() == "yes"


class Chatflow:
    def __init__(self, io=None, interactive=True):
        self.session = Session()
        self.interactive = interactive
        self.out = print

    # -- STATE 0 -----------------------------------------------------------
    def state0_greeting(self):
        self.session.set_state(0)
        self.out("ReactOS Build & UI Customization Assistant")
        self.out("")
        self.out("I can help you update/build ReactOS from source and try to")
        self.out("make its UI/UX resemble Windows 11. Scope and honest limits:")
        self.out("")
        self.out(warn(ALPHA_WARNING))
        self.out("")
        self.out(warn("There is no official Windows 11 theme for ReactOS, and no"))
        self.out("> natively supported Windows 11 UI. Anything resembling it is a")
        self.out("> theme or a source change, both unofficial and both experimental.")
        self.out("")
        goal = "B" if not self.interactive else ask(
            "Do you want to (A) update/build ReactOS, (B) customize the UI, "
            "or (C) both?", default="C", choices=["A", "B", "C"])
        key = (goal or "C").strip().upper()[:1]
        if key not in ("A", "B", "C"):
            self.out(f"  {goal!r} is not A, B or C - assuming C (both)")
            key = "C"
        self.session["goal"] = {"A": "update", "B": "ui", "C": "both"}[key]
        self.state1_environment_check()

    # -- STATE 1 -----------------------------------------------------------
    def state1_environment_check(self):
        self.session.set_state(1)
        self.out("\n[1] Environment check")
        if self.interactive:
            self.session["host_os"] = ask(
                "  Host OS \u2014 Windows or Linux?", default="Linux",
                choices=["Windows", "Linux"])
            self.session["rosbe_installed"] = ask(
                "  Is RosBE installed? (yes/no)", default="no",
                choices=["yes", "no"]).lower() == "yes"
            self.session["vm_available"] = ask(
                "  Do you have a VM to test in? (yes/no)", default="yes",
                choices=["yes", "no"]).lower() == "yes"
            try:
                self.session["disk_space_gb"] = int(
                    ask("  Free disk space in GB?", default="40"))
            except ValueError:
                self.session["disk_space_gb"] = 0
        else:
            self.session["host_os"] = "Linux"
            self.session["rosbe_installed"] = True
            self.session["vm_available"] = True
            self.session["disk_space_gb"] = 60

        self.session["git_installed"] = bool(tools.shutil.which("git"))
        missing = []
        if not self.session["git_installed"]:
            missing.append("git")
        if not self.session["rosbe_installed"]:
            missing.append("RosBE")
        if missing:
            self.out(f"  missing: {', '.join(missing)}")
            self.out("  RosBE 2.2.1 \u2014 Windows:")
            self.out("    https://sourceforge.net/projects/reactos/files/"
                     "RosBE-Windows/i386/2.2.1/")
            self.out("  RosBE 2.2.1 \u2014 Unix:")
            self.out("    https://sourceforge.net/projects/reactos/files/"
                     "RosBE-Unix/2.2.1/")
            self.out("  On Linux, RosBE also provides the cross compiler; a bare")
            self.out("  system gcc cannot build ReactOS.")
        if self.session["disk_space_gb"] < 20:
            self.out(warn(f"only {self.session['disk_space_gb']} GB free; a "
                          f"ReactOS build wants 20 GB or more"))
        self.state2_goal_router()

    # -- STATE 2 -----------------------------------------------------------
    def state2_goal_router(self):
        self.session.set_state(2)
        goal = self.session["goal"]
        if goal in ("update", "both"):
            self.state3_update_reactos()
        if goal in ("ui", "both"):
            self.state5_ui_intro()

    # -- STATE 3 -----------------------------------------------------------
    def state3_update_reactos(self):
        self.session.set_state(3)
        self.out("\n[3] Update / build ReactOS")
        repo = self.session["repo_path"] or "."
        if not os.path.isdir(os.path.join(repo, ".git")):
            self.out("  No checkout found. Cloning:")
            self.out("\n```bash")
            self.out(f"git clone https://github.com/reactos/reactos.git")
            self.out("```")
            if self.interactive and confirm("  Clone now?"):
                res = tools.git_clone(destination="./reactos", confirm=True)
                self.session["repo_path"] = res["destination"]
                repo = res["destination"]
            else:
                repo = "./reactos"
        else:
            res = tools.git_pull(repo, confirm=True)
            self.session["repo_path"] = os.path.abspath(repo)
            self.out(f"  branch {res['branch']}: ahead {res['ahead']}, "
                     f"behind {res['behind']}, dirty {res['dirty']}")
            self.out(f"  {res['advice']}")
            self.out("")
            self.out("  Note: `git pull <url>` is not the right form \u2014 it can")
            self.out("  merge an unexpected ref. Use a remote and a branch.")

        self.out("\n  Configure and build:")
        self.out("\n```bash")
        self.out("# inside an active RosBE shell")
        self.out("mkdir -p output-MinGW-i386 && cd output-MinGW-i386")
        self.out("../configure.sh                 # Windows RosBE: ..\\\\configure.cmd")
        self.out("ninja bootcd                    # produces bootcd.iso")
        self.out("```")
        self.out("")
        self.out("  The directory name follows the arch, so it is")
        self.out("  output-MinGW-i386, -amd64, -arm or -arm64, not always -i386.")
        self.session["build_dir"] = os.path.abspath("output-MinGW-i386")
        self.state4_verify_build()

    # -- STATE 4 -----------------------------------------------------------
    def state4_verify_build(self):
        self.session.set_state(4)
        self.out("\n[4] Verify the build")
        iso = os.path.join(self.session["build_dir"] or "", "bootcd.iso")
        if self.interactive:
            done = ask("  Did `ninja bootcd` finish without errors? (yes/no)",
                       default="yes", choices=["yes", "no"]).lower() == "yes"
        else:
            done = True
        if not done:
            self.state8_troubleshoot()
            return
        self.session["iso_path"] = iso
        self.out(f"  ISO expected at: {iso}")
        if os.path.exists(iso):
            res = tools.vm_test(iso, "qemu")
            self.out(f"  ISO9660 signature: "
                     f"{'present' if res['iso9660_valid'] else 'MISSING'}")
            self.out(f"  suggested command: {' '.join(res['command'])}")
        else:
            self.out("  (not present yet \u2014 nothing has been built here)")
        self.out("")
        self.out(warn("Test the ISO in a VM first. Do not install over a disk"))
        self.out("> you care about.")
        if not self.interactive:
            self.out("  -> simulated success, continuing")
        if self.session["goal"] in ("update", "both"):
            self.state5_ui_intro()

    # -- STATE 5 -----------------------------------------------------------
    def state5_ui_intro(self):
        self.session.set_state(5)
        self.out("\n[5] UI customisation \u2014 what is actually possible")
        self.out("")
        for line in [
            "ReactOS targets Windows Server 2003 compatibility. Its theming",
            "engine (uxtheme + .msstyles) works but is incomplete, and the",
            "look you are after depends on machinery ReactOS does not have:",
            "",
            "  * dwmapi is a stub - there is no desktop compositor, so no Mica,",
            "    no acrylic, no antialiased rounded window corners, no real",
            "    shadows and no smooth window animations;",
            "  * window frames are drawn from theme bitmaps, and GDI regions are",
            "    1-bit, so rounded top-level corners would be jagged anyway;",
            "  * many controls are simply not themed yet.",
            "",
            "So 'as close as possible' has a hard ceiling. You can get the",
            "colours, fonts, buttons, caption bars and scrollbars close. You",
            "cannot get the compositor-driven parts.",
        ]:
            self.out("  " + line)
        self.out("")
        self.out(warn("No official Windows 11 theme exists for ReactOS. Any"))
        self.out("> theme you find is unofficial, and a bad theme can leave")
        self.out("> windows unopenable \u2014 have a rollback ready.")
        self.out("")
        self.out(METHOD_TABLE)
        method = "C" if not self.interactive else ask(
            "\n  Choose A, B or C", default="B", choices=["A", "B", "C"])
        self.session["ui_method"] = method.upper()
        self.out("")
        self.state6_ui_execution()

    # -- STATE 6 -----------------------------------------------------------
    def state6_ui_execution(self):
        self.session.set_state(6)
        m = self.session["ui_method"]
        self.out(f"[6] Method {m}")

        if m == "A":
            res = tools.search_theme("windows 11")
            self.out(f"  searched_the_web: {res['searched_the_web']}")
            self.out(f"  {res['disclaimer']}")
            for c in res["results"]:
                self.out(f"    - {c['name']}")
                self.out(f"      licence: {c['licence']}")
                self.out(f"      {c['notes']}")
            self.out("")
            self.out(warn("Do NOT patch or replace uxtheme.dll on ReactOS."))
            self.out("> ReactOS's uxtheme does not check theme signatures, so")
            self.out("> that step from XP guides is unnecessary here \u2014 and")
            self.out("> swapping in a Windows uxtheme.dll would break the DLL")
            self.out("> against our win32u/ntuser, which is a very different")
            self.out("> implementation.")
            self.out("")
            self.out("  To try a theme, drop it in and select it:")
            self.out("\n```bash")
            self.out("# from the VM: copy the .msstyles under")
            self.out("#   %SystemRoot%/Resources/Themes/<Name>/")
            self.out("# then pick it in Display Properties, or set it directly:")
            self.out("reg add \"HKCU\\\\Software\\\\Microsoft\\\\Windows\\\\"
                     "CurrentVersion\\\\Themes\" /v CurrentTheme /t REG_SZ /d "
                     "\"%SystemRoot%\\\\Resources\\\\Themes\\\\<Name>\\\\<name>.msstyles\"")
            self.out("```")
            self.out("")
            self.out("  Rollback: set CurrentTheme back to an empty value and reboot.")

        elif m == "B":
            self.out("  Source-level changes, by surface:")
            self.out("")
            for p, what in [
                ("media/themes/<Name>/<name>.msstyles/",
                 "the theme itself: .rc resource script, bitmaps/, and the "
                 "textfiles/*.INI holding colours, fonts and metrics"),
                ("dll/win32/uxtheme/",
                 "how theme parts are drawn (nonclient.c for frames, draw.c "
                 "for controls)"),
                ("base/shell/explorer/",
                 "the shell: taskband.cpp (taskbar), startmnu.cpp (Start menu)"),
                ("win32ss/user/ntuser/",
                 "window manager: frame drawing, and where a rounded-window "
                 "region would go"),
            ]:
                self.out(f"    {p}")
                self.out(f"      {what}")
            self.out("")
            self.out("  A theme's design tokens live in textfiles/*.INI:")
            self.out("\n```ini")
            self.out("[SysMetrics]")
            self.out("CaptionFont = Segoe UI, 9, Bold")
            self.out("CaptionBarHeight = 24")
            self.out("ActiveCaption = 32 32 32")
            self.out("Btnface = 243 243 243")
            self.out("```")
            self.out("")
            self.out("  Validate before you build:")
            self.out("\n```bash")
            self.out("python3 contrib/reactos-w11-assistant/validate_msstyles.py \\")
            self.out("    media/themes/<Name>/<name>.msstyles")
            self.out("```")
            self.out("")
            self.out(warn("A theme that fails validation will silently fall back"))
            self.out("> to the classic look, which is the usual cause of 'nothing")
            self.out("> happened'.")

        else:
            self.out("  Hybrid = theme + icons + cursors + wallpaper."
                     " Lowest effort, medium risk.")
            self.out("")
            self.out("    Theme     a validated .msstyles (method A or B)")
            self.out("    Icons     ReactOS reads icons from shell32, "
                     "comctl32 and imageres; replacing them is a resource edit")
            self.out("    Cursors   HKCU\\Control Panel\\Cursors, no rebuild needed")
            self.out("    Wallpaper plain image, no rebuild needed")
            self.out("    Shell     third-party Windows shells are the riskiest")
            self.out("              part; compatibility with our shell32 is unknown")
            self.out("")
            self.out(warn("A shell replacement is the one step most likely to"))
            self.out("> leave you with no working desktop. Keep a VM snapshot.")

        self.state7_verify_ui()

    # -- STATE 7 -----------------------------------------------------------
    def state7_verify_ui(self):
        self.session.set_state(7)
        self.out("\n[7] Verify")
        self.out("  Reboot the VM, then check: did the theme apply, and is")
        self.out("  Explorer still running?")
        if not self.interactive:
            self.out("  -> simulated success")
            self.state9_end()
            return
        ok = ask("  Applied successfully? (yes/no)", default="yes",
                 choices=["yes", "no"]).lower() == "yes"
        if ok:
            self.state9_end()
        else:
            self.state8_troubleshoot()

    # -- STATE 8 -----------------------------------------------------------
    def state8_troubleshoot(self):
        self.session.set_state(8)
        self.out("\n[8] Troubleshoot")
        err = "?" if not self.interactive else ask(
            "  Paste the exact error, or describe the symptom")
        self.session["errors"].append(err)
        self.out("")
        self.out("  Theme did not apply:")
        self.out("    - validate it: a missing PACKTHEM_VERSION/COLORNAMES/"
                 "SIZENAMES/FILERESNAMES/TEXTFILE")
        self.out("      resource makes uxtheme fall back silently")
        self.out("    - confirm PACKTHEM_VERSION is 3")
        self.out("    - check the path under %SystemRoot%/Resources/Themes")
        self.out("")
        self.out("  Explorer crashes after theming:")
        self.out("    - boot and clear HKCU\\...\\Themes\\CurrentTheme")
        self.out("    - if it will not boot, use the F8 menu / last known good")
        self.out("")
        self.out("  Build errors:")
        self.out("    - activate RosBE, then reconfigure; a stale CMakeCache")
        self.out("      after switching toolchain is the usual cause")
        self.out("    - `Could not detect RosBE.` from configure.sh means the")
        self.out("      RosBE environment is not active in that shell")
        self.out("")
        self.out("  Rollback for a theme file:")
        self.out("\n```bash")
        self.out("python3 contrib/reactos-w11-assistant/tools.py   # backup_file first")
        self.out("```")
        self.state9_end()

    # -- STATE 9 -----------------------------------------------------------
    def state9_end(self):
        self.session.set_state(9)
        self.out("\n[9] Summary")
        s = self.session
        self.out(f"  goal            {s['goal']}")
        self.out(f"  host            {s['host_os']}")
        self.out(f"  RosBE           {'yes' if s['rosbe_installed'] else 'no'}")
        self.out(f"  repo            {s['repo_path']}")
        self.out(f"  build dir       {s['build_dir']}")
        self.out(f"  ISO             {s['iso_path']}")
        self.out(f"  UI method       {s['ui_method']}")
        if s["errors"]:
            self.out(f"  errors seen     {len(s['errors'])}")
        self.out("")
        self.out("  Next steps: build in RosBE, test in a VM, and keep snapshots.")
        self.out("")
        self.out(warn("ReactOS is alpha. Back up your VMs and never install"))
        self.out("> over a disk you care about.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true",
                    help="replay the documented example flow non-interactively")
    ap.add_argument("--state", action="store_true",
                    help="print the session state schema and exit")
    args = ap.parse_args()

    if args.state:
        print(Session().dump())
        return 0

    flow = Chatflow(interactive=not args.demo)
    flow.state0_greeting()
    if args.demo:
        print("\n--- session state ---")
        print(flow.session.dump())
    return 0


if __name__ == "__main__":
    sys.exit(main())
