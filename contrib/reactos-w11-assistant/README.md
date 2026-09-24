# ReactOS Build & UI Customization Assistant

A working implementation of the "Full-Stack ChatFlow Prompt" for updating
ReactOS and attempting a Windows 11 look. The state machine, the tools and the
validator are real and runnable; the parts that cannot work in a given
environment say so rather than pretending.

> **Fork-local tooling.** This directory is not part of the ReactOS build — no
> `CMakeLists.txt` references it and no build target includes it. It is here so
> that the assistant and its corrections are versioned alongside the tree it
> talks about. Drop it before sending anything upstream.

## Run it

```bash
python3 contrib/reactos-w11-assistant/chatflow.py           # interactive
python3 contrib/reactos-w11-assistant/chatflow.py --demo     # replay the example flow
python3 contrib/reactos-w11-assistant/chatflow.py --state    # dump the session schema

# validate a theme (works on unbuilt sources and on built .msstyles files)
python3 contrib/reactos-w11-assistant/validate_msstyles.py \
    media/themes/Modern/modern.msstyles
```

Only the Python standard library is required. `tools.py` and
`validate_msstyles.py` are importable on their own.

## What each piece is

| File | |
|---|---|
| `chatflow.py` | the ten-state machine, session state, guardrails, CLI |
| `tools.py` | the eight tools, really implemented |
| `validate_msstyles.py` | theme validator — source trees and packed PE resource directories |
| `SYSTEM_PROMPT.md` | the operating prompt, corrected |

All five themes in `media/themes/` currently validate clean:

| Theme | Result |
|---|---|
| Lautus | all required resources present |
| Lunar | all required resources present |
| Mizu | all required resources present |
| Modern | all required resources present (6 schemes incl. Dark) |
| Blackshade | all required resources present (not built — placeholder) |

## Corrections to the original prompt

The original draft was written from general Windows theming knowledge, and
several of its load-bearing claims are wrong for ReactOS. These are the ones
that would have sent someone down a broken path.

### 1. Patching `uxtheme.dll` is not a step here — and would be harmful

The original listed "uxtheme.dll patching / registry tweaks" as part of
method A. That is an **XP** requirement: XP only loads digitally signed
themes, so guides patch the DLL to skip the check. ReactOS's `uxtheme`
contains no signature validation at all (there is no `WinVerifyTrust`, no
signature or licence check anywhere in `dll/win32/uxtheme/`).

So the step is unnecessary. Worse, following it *by copying in a Windows
`uxtheme.dll`* would break: ReactOS's `uxtheme` is bound to our own
`win32u`/`ntuser`, not to Win32's internals.

### 2. `git pull <url>` is the wrong form

The original used `git pull https://github.com/reactos/reactos.git`. That
does not do what it looks like and can merge an unexpected ref. `git_pull`
now fetches explicitly, compares against a ref it resolves, and reports
ahead/behind before suggesting `git merge --ff-only`.

### 3. The build directory is not always `output-MinGW-i386`

The original hardcoded `output-MinGW-i386`. `configure.sh` derives it from
the architecture, so it is `-amd64`, `-arm` or `-arm64` for those targets.

### 4. `./configure.sh` needs RosBE active

The original implied `configure.sh` just runs. It exits with
`Could not detect RosBE.` when `ROS_ARCH` is unset, and a system `gcc`
cannot build ReactOS — RosBE supplies the cross compiler. `run_rosbe_configure`
now detects both conditions and explains them instead of failing obscurely.

### 5. Third-party `.msstyles` can work — but not the way the table implied

The original filed third-party themes under "stability: low" without saying
why. The real reason is worth knowing, because it is both better and worse
than it sounds.

**Better:** the packed format matches. `dll/win32/uxtheme/msstyles.c` reads
`PACKTHEM_VERSION` / `COLORNAMES` / `SIZENAMES` / `FILERESNAMES` / `TEXTFILE`
— the same resource layout Windows XP uses. A version-3 XP theme is
structurally loadable.

**Worse:** failure is *silent*. A theme missing any required resource makes
uxtheme fall back to the classic look with no error, which is why "I installed
a theme and nothing happened" is the standard complaint. And even a valid
theme only restyles the controls our `uxtheme` actually implements.

### 6. "As closely as possible" has a hard ceiling, and it is not a theming limit

The original's method list implied source modification could reach Windows 11.
For the theme-able parts, yes. But ReactOS's `dwmapi` is a **338-line stub** —
there is no desktop compositor — so the parts that make Windows 11 *look* like
Windows 11 are unreachable by theming:

| Windows 11 signature | Needs | ReactOS |
|---|---|---|
| Mica / Acrylic | compositor | no |
| Antialiased rounded window corners | compositor | no |
| Real drop shadows | compositor | no |
| Smooth window animations | compositor | no |

Rounded top-level window corners additionally cannot be done well without it:
they would be applied as GDI regions, which are 1-bit, so the corners would be
jagged rather than antialiased. `uxtheme` also never applies a window region
for rounding today.

The honest summary — and what the assistant now says up front — is that you
can get colours, fonts, control shapes, caption bars, scrollbars, icons,
cursors and wallpaper close; you cannot get the compositor-driven parts at all.

### 7. `search_theme` cannot search

The original specified a search tool. There is no network access here, so
`search_theme` returns a curated catalogue, reports `searched_the_web: False`,
and refuses to present itself as search results.

## Testing status

What has actually been exercised:

- `validate_msstyles.py` against all five themes in the tree (all pass), and
  against a non-theme file (correctly rejected as not a PE)
- `tools.git_pull` against this checkout — it correctly reported the local
  tree as dirty and 4 commits behind the remote
- `tools.backup_file` — wrote a real backup and manifest entry
- `tools.vm_test` on a non-ISO — correctly reported the missing ISO9660 signature
- consent gating on every destructive tool
- `chatflow.py --demo` end-to-end through states 0, 1, 5, 6, 7, 9

What has **not** been exercised, because this environment cannot do it:

- `git_clone`, `run_rosbe_configure`, `run_ninja_build` — no RosBE, no cross
  compiler, and a ReactOS build does not fit in the available memory
- `vm_test` has never had a real ISO to check
- any actual theme being loaded by a running ReactOS

So the assistant is verified to the extent the environment allows and no
further. Treat a first real run as a first real run.
