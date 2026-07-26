"""Installer for the Maya Anime Hair Sweep Tool.

Two ways to run this from inside Maya:

1) Drag ``install.py`` from your file browser into any Maya viewport.
2) From the Script Editor (Python tab)::

       exec(open(r"C:/path/to/install.py").read())

Either way this installer:

* Fetches every ``maya_hair_tool/*.py`` file fresh from GitHub, using a
  SHA-pinned raw URL so the CDN can never serve stale content
  (patterns doc §1-7).
* Writes each file atomically (Windows read-only cleared, tmp + os.replace)
  so a mid-install crash never leaves half-written source
  (patterns doc §1-3, §1-4).
* Wipes any ``__pycache__/*.pyc`` for the package so stale bytecode does
  not shadow the freshly copied source (patterns doc §1-5).
* Flushes ``maya_hair_tool`` from ``sys.modules`` so the next import reads
  from disk — no Maya restart needed (patterns doc §1-6).
* Adds a shelf button on the active shelf. Left-click launches; right-click
  has an "Update from GitHub" menu that re-runs this installer without
  another drag (patterns doc §1-8).

Structure of this installer follows the "single .md self-contained
template" in ``maya-hot-update-patterns.md`` (§5-A + §6-5 multi-file
variant).
"""

from __future__ import annotations

import os
import re
import sys


# ─── CUSTOMIZE ────────────────────────────────────────────────────────────
_GITHUB_OWNER = "ogshaw03"
_GITHUB_REPO = "Maya_Anime_HairSweepTool"
_GITHUB_BRANCH = "main"

_PACKAGE = "maya_hair_tool"            # top-level package folder name
_SHELF_BUTTON_LABEL = "HairTool"       # short label on the shelf button

# Every file the package needs on disk. **Add new modules here** whenever
# the package grows — otherwise the next `Update from GitHub` will pull
# an incomplete package and `__init__.py` will fail to import a missing
# submodule (patterns doc §1-10).
_REMOTE_FILES = (
    _PACKAGE + "/__init__.py",
    _PACKAGE + "/constants.py",
    _PACKAGE + "/sweep_utils.py",
    _PACKAGE + "/hair.py",
    _PACKAGE + "/duplicate.py",
    _PACKAGE + "/batch.py",
    _PACKAGE + "/ui.py",
)
# ─── END CUSTOMIZE ────────────────────────────────────────────────────────


_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_GITHUB_API = "https://api.github.com/repos/{0}/{1}".format(
    _GITHUB_OWNER, _GITHUB_REPO)
_GITHUB_RAW_BASE = "https://raw.githubusercontent.com/{0}/{1}".format(
    _GITHUB_OWNER, _GITHUB_REPO)


# --------------------------------------------------------------------------- #
# Force-overwrite helpers (Windows-safe)  — patterns doc §1-3, §1-4
# --------------------------------------------------------------------------- #

def _force_writable(path):
    import stat
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass


def _atomic_write_bytes(target, data):
    """Overwrite ``target`` atomically.

    Either the previous complete file OR the new complete file exists on
    disk — no half-written garbage on cancel / power loss / disk full.
    """
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    if os.path.exists(target):
        _force_writable(target)
    tmp = target + ".tmp_install"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except Exception:
            pass
    os.replace(tmp, target)


# --------------------------------------------------------------------------- #
# Module acquisition
# --------------------------------------------------------------------------- #

def _resolve_latest_sha():
    """SHA-pinned raw URLs are the only reliable cache-buster for
    ``raw.githubusercontent.com`` — its CDN caches by path only."""
    import json
    import random
    import time
    from urllib.request import Request, urlopen

    salt = "{0:.6f}_{1}".format(time.time(), random.randint(0, 2 ** 32))
    req = Request("{0}/branches/{1}?_={2}".format(
        _GITHUB_API, _GITHUB_BRANCH, salt),
        headers={
            "Accept": "application/vnd.github+json",
            "Cache-Control": "no-cache",
            "User-Agent": "{0}-installer/{1}".format(_PACKAGE, salt),
        })
    try:
        with urlopen(req, timeout=30) as resp:
            sha = json.loads(resp.read().decode("utf-8"))["commit"]["sha"]
        print("[{0}] resolved {1} -> {2}".format(
            _PACKAGE, _GITHUB_BRANCH, sha[:10]))
        return sha
    except Exception as exc:
        print("[{0}] SHA lookup failed ({1}); falling back to branch "
              "name (may hit CDN cache)".format(_PACKAGE, exc))
        return _GITHUB_BRANCH


def _fetch_package(dest_root):
    """Default: always pull from GitHub.

    Developers who want to iterate on the local checkout set
    ``MAYA_HAIR_TOOL_USE_LOCAL=1`` — that copies the on-disk files
    next to this ``install.py`` instead.
    """
    from urllib.request import Request, urlopen

    env_flag = _PACKAGE.upper() + "_USE_LOCAL"
    use_local = os.environ.get(env_flag) == "1"

    if use_local:
        print("[{0}] {1}=1 -> copying local files under {2}".format(
            _PACKAGE, env_flag, _REPO_ROOT))
        for rel in _REMOTE_FILES:
            src = os.path.join(_REPO_ROOT, rel.replace("/", os.sep))
            if not os.path.isfile(src):
                raise RuntimeError(
                    "Local file missing: {0}".format(src))
            with open(src, "rb") as fh:
                data = fh.read()
            target = os.path.join(dest_root, rel.replace("/", os.sep))
            _atomic_write_bytes(target, data)
            print("[{0}]   -> {1} ({2} bytes)".format(
                _PACKAGE, target, len(data)))
        return

    sha = _resolve_latest_sha()
    for rel in _REMOTE_FILES:
        url = "{0}/{1}/{2}".format(_GITHUB_RAW_BASE, sha, rel)
        print("[{0}] downloading {1}".format(_PACKAGE, url))
        req = Request(url, headers={
            "Cache-Control": "no-cache",
            "User-Agent": "{0}-installer/{1}".format(_PACKAGE, sha[:10]),
        })
        try:
            data = urlopen(req, timeout=30).read()
        except Exception as exc:
            raise RuntimeError(
                "Failed to download {0}: {1}".format(url, exc))
        target = os.path.join(dest_root, rel.replace("/", os.sep))
        _atomic_write_bytes(target, data)
        print("[{0}]   -> {1} ({2} bytes)".format(
            _PACKAGE, target, len(data)))


# --------------------------------------------------------------------------- #
# Post-install: verify, clean pycache, flush imports
# --------------------------------------------------------------------------- #

def _verify_install(dest_root):
    for rel in _REMOTE_FILES:
        p = os.path.join(dest_root, rel.replace("/", os.sep))
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            raise RuntimeError(
                "Install verification failed — {0} missing/empty".format(p))


def _clean_pycache(dest_root):
    """Remove any stale .pyc for this package — patterns doc §1-5."""
    import shutil
    import time
    pycache = os.path.join(dest_root, _PACKAGE, "__pycache__")
    if not os.path.isdir(pycache):
        return

    def _on_error(func, path, exc_info):
        _force_writable(path)
        try:
            func(path)
        except Exception:
            pass

    for _ in range(3):
        try:
            shutil.rmtree(pycache, onerror=_on_error)
            if not os.path.isdir(pycache):
                return
        except Exception:
            time.sleep(0.2)


def _flush_imports():
    """Drop the package from sys.modules — patterns doc §1-6."""
    for name in list(sys.modules):
        if name == _PACKAGE or name.startswith(_PACKAGE + "."):
            sys.modules.pop(name, None)


def _read_installed_version(dest_root):
    p = os.path.join(dest_root, _PACKAGE, "__init__.py")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(
                    r'\s*__version__\s*=\s*[\'"]([^\'"]+)[\'"]', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return "(unknown)"


def _close_existing_window():
    try:
        from maya import cmds
        window_name = _PACKAGE + "Win"
        if cmds.window(window_name, exists=True):
            cmds.deleteUI(window_name)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Shelf button (with right-click Update popup — patterns doc §1-8)
# --------------------------------------------------------------------------- #

_SHELF_LAUNCH_CMD = (
    "# Auto-generated by {0} install.py\n"
    "import sys\n"
    "for _m in [k for k in list(sys.modules)\n"
    "           if k == {0!r} or k.startswith({0!r} + '.')]:\n"
    "    sys.modules.pop(_m, None)\n"
    "import {0} as _t; _t.show()\n"
).format(_PACKAGE)


_SHELF_UPDATE_CMD = (
    "# Auto-generated by {pkg} install.py\n"
    "import json, urllib.request\n"
    "_api = 'https://api.github.com/repos/{owner}/{repo}/branches/{branch}'\n"
    "_sha = json.loads(urllib.request.urlopen(_api, timeout=30)"
    ".read())['commit']['sha']\n"
    "_u = 'https://raw.githubusercontent.com/{owner}/{repo}/' + _sha "
    "+ '/install.py'\n"
    "print('[{pkg}] update via SHA', _sha[:10])\n"
    "exec(compile(urllib.request.urlopen(_u, timeout=30).read(),\n"
    "             'install.py (from GitHub)', 'exec'),\n"
    "     {{'__name__': 'install', '__file__': '<github>'}})\n"
).format(pkg=_PACKAGE, owner=_GITHUB_OWNER, repo=_GITHUB_REPO,
         branch=_GITHUB_BRANCH)


def _add_shelf_button():
    from maya import cmds, mel

    top_shelf = mel.eval("$tmp = $gShelfTopLevel")
    if not top_shelf or not cmds.tabLayout(top_shelf, exists=True):
        return
    current = cmds.tabLayout(top_shelf, query=True, selectTab=True)
    if not current:
        return

    for child in cmds.shelfLayout(current, query=True, childArray=True) or []:
        try:
            if cmds.shelfButton(child, query=True, label=True) == \
                    _SHELF_BUTTON_LABEL:
                cmds.deleteUI(child)
        except Exception:
            pass

    button = cmds.shelfButton(
        parent=current,
        label=_SHELF_BUTTON_LABEL,
        annotation="Left-click: launch.  Right-click: update from GitHub.",
        image="pythonFamily.png",
        imageOverlayLabel=_SHELF_BUTTON_LABEL[:5],
        command=_SHELF_LAUNCH_CMD,
        sourceType="python",
    )
    popup = cmds.popupMenu(parent=button, button=3)
    cmds.menuItem(parent=popup, label="Launch Tool",
                  command=_SHELF_LAUNCH_CMD, sourceType="python")
    cmds.menuItem(parent=popup, divider=True)
    cmds.menuItem(parent=popup, label="Update from GitHub",
                  command=_SHELF_UPDATE_CMD, sourceType="python")


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #

def install():
    from maya import cmds

    user_scripts = cmds.internalVar(userScriptDir=True).rstrip("/\\")
    if not os.path.isdir(user_scripts):
        os.makedirs(user_scripts)

    prev_version = _read_installed_version(user_scripts)

    _close_existing_window()
    _fetch_package(user_scripts)
    _clean_pycache(user_scripts)
    _verify_install(user_scripts)
    _flush_imports()

    if user_scripts not in sys.path:
        sys.path.insert(0, user_scripts)

    _add_shelf_button()
    new_version = _read_installed_version(user_scripts)

    bar = "=" * 55
    print("[{0}] {1}".format(_PACKAGE, bar))
    print("[{0}] installed to:      {1}".format(_PACKAGE, user_scripts))
    print("[{0}] previous version:  {1}".format(_PACKAGE, prev_version))
    print("[{0}] current  version:  {1}".format(_PACKAGE, new_version))
    print("[{0}] {1}".format(_PACKAGE, bar))

    try:
        cmds.confirmDialog(
            title=_SHELF_BUTTON_LABEL,
            message=("Installed to:\n{0}\n\n"
                     "Version: {1} -> {2}\n\n"
                     "The '{3}' shelf button has been refreshed. "
                     "Left-click to launch, right-click for "
                     "Update-from-GitHub.".format(
                         user_scripts, prev_version, new_version,
                         _SHELF_BUTTON_LABEL)),
            button=["OK"])
    except Exception:
        pass
    return user_scripts


def onMayaDroppedPythonFile(*_args):
    install()


# ``exec(open(...).read())`` from the Script Editor bypasses
# ``__name__ == '__main__'`` and ``onMayaDroppedPythonFile``. Run
# install() eagerly here so both entry points work. install() is
# idempotent (existing files wiped before write), so drag-drop's
# double-run is harmless.
try:
    from maya import cmds as _cmds  # noqa: F401
    install()
except ImportError:
    pass
