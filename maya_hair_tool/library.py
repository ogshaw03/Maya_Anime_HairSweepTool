"""Hair library — save / list / import reusable hair strand presets.

Presets are stored as one ``.ma`` (Maya ASCII) fragment per strand
plus an optional ``.png`` thumbnail next to it, both under the same
library directory. Default location is::

    <userScriptDir>/maya_hair_tool_library/

Override with the ``MAYA_HAIR_TOOL_LIBRARY`` environment variable —
handy for pointing a whole studio at a shared network drive.

Save flow
    1. User selects a hair mesh in the scene.
    2. ``save_hair_to_library(name)`` gathers the strand's mesh
       transform + guide curve (and any custom profile curve) and
       writes ``<library>/<name>.ma`` via ``cmds.file(
       exportSelected=True, type='mayaAscii')``. The upstream
       ``sweepMeshCreator`` node comes along automatically because
       exportSelected walks history.
    3. A best-effort playblast snapshot goes to
       ``<library>/<name>.png``.

Import flow
    * ``import_hair_from_library(ma_path)`` runs ``cmds.file(
      i=True, namespace=…)`` so re-importing the same preset
      several times doesn't collide.
    * The freshly-imported mesh transform is reparented under
      ``HairGroup`` (or a user-supplied group) so it lands in the
      normal Hair Builder hierarchy.

Everything else in this module is pure filesystem plumbing — no
custom node types, so a scene saved after an import opens fine on
machines that don't have this tool installed.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:  # pragma: no cover — allow import outside Maya
    cmds = None

from . import constants as C
from . import hair
from . import sweep_utils as su


# --------------------------------------------------------------------------- #
# Library filesystem layout
# --------------------------------------------------------------------------- #

LIBRARY_ENV = "MAYA_HAIR_TOOL_LIBRARY"


def library_root() -> str:
    """Return (and lazily create) the directory that holds all
    ``.ma`` / ``.png`` preset pairs. Set ``MAYA_HAIR_TOOL_LIBRARY``
    to point somewhere else."""
    root = os.environ.get(LIBRARY_ENV)
    if not root:
        if cmds is not None:
            scripts = cmds.internalVar(
                userScriptDir=True).rstrip("/\\")
            root = os.path.join(scripts, "maya_hair_tool_library")
        else:
            root = os.path.expanduser(
                "~/maya_hair_tool_library")
    if not os.path.isdir(root):
        try:
            os.makedirs(root)
        except Exception:
            pass
    return root


def list_library_entries() -> List[Tuple[str, str, str]]:
    """Return one ``(name, ma_path, png_path_or_empty)`` per preset,
    sorted by name (case-insensitive)."""
    root = library_root()
    entries = []
    try:
        files = os.listdir(root)
    except Exception:
        return []
    for filename in files:
        if not filename.lower().endswith(".ma"):
            continue
        name = filename[:-3]
        ma_path = os.path.join(root, filename)
        png_path = os.path.join(root, name + ".png")
        entries.append(
            (name, ma_path,
             png_path if os.path.isfile(png_path) else ""))
    entries.sort(key=lambda e: e[0].lower())
    return entries


def _sanitize_name(name: str) -> str:
    """Reduce ``name`` to a filesystem-safe basename. Removes path
    separators, control chars, and disallowed Windows chars; keeps
    ASCII alphanumerics + ``_ - .`` plus any full-width JIS chars
    which are legal on Windows/macOS filesystems."""
    if not name:
        return ""
    # Strip path chars.
    name = name.replace("/", "_").replace("\\", "_")
    # Drop control chars.
    name = re.sub(r"[\x00-\x1f]", "", name)
    # Drop the Windows-reserved punctuation.
    name = re.sub(r'[<>:"|?*]', "", name)
    # Trim whitespace + dots.
    name = name.strip().strip(".")
    return name


# --------------------------------------------------------------------------- #
# Save
# --------------------------------------------------------------------------- #

def save_hair_to_library(
    name: str,
    thumbnail: bool = True,
    creator: Optional[str] = None,
) -> str:
    """Export the currently-selected hair strand (or the one whose
    ``sweepMeshCreator`` is ``creator``) as ``<name>.ma`` in the
    library. Returns the saved ``.ma`` path.

    Raises RuntimeError on user-visible failures (nothing selected /
    multiple selection / bad name / no guide curve) so the caller
    can turn each into a warning dialog."""
    if cmds is None:
        raise RuntimeError("save_hair_to_library requires Maya.")

    if creator is None:
        creators = su.sweep_creators_from_selection()
        if not creators:
            raise RuntimeError(
                "毛束が選択されていません。カーブ / メッシュ / "
                "sweepMeshCreator のいずれかを 1 つ選択してください。")
        if len(creators) > 1:
            raise RuntimeError(
                "複数の毛束が選択されています。1 本ずつ保存"
                "してください。")
        creator = creators[0]

    mesh = su.mesh_from_creator(creator)
    curve = su.curve_from_creator(creator)
    if not curve:
        raise RuntimeError(
            "guide curve が見つかりません。毛束の履歴が壊れて"
            "いる可能性があります。")

    safe = _sanitize_name(name)
    if not safe:
        raise RuntimeError("プリセット名が空です。")

    root = library_root()
    ma_path = os.path.join(root, safe + ".ma")
    png_path = os.path.join(root, safe + ".png")

    # exportSelected walks upstream construction history, so
    # including the mesh transform is enough to drag the
    # sweepMeshCreator along. We also include the guide curve
    # explicitly so its shape gets exported (curve.worldSpace →
    # creator.inCurveArray is upstream, so it'd be picked up
    # anyway, but being explicit is safer).
    to_export = [n for n in (mesh, curve) if n and cmds.objExists(n)]
    if not to_export:
        raise RuntimeError(
            "エクスポート対象がありません (mesh と curve の"
            "どちらも見つかりません)")

    prev_sel = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(to_export, replace=True)
        cmds.file(
            ma_path,
            exportSelected=True,
            type="mayaAscii",
            force=True,
            constructionHistory=True,
            channels=True,
            constraints=True,
            expressions=False,
            shader=True,
        )
    finally:
        try:
            if prev_sel:
                cmds.select(prev_sel, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass

    if thumbnail:
        try:
            _snapshot_thumbnail(png_path, focus=to_export)
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] サムネイル生成失敗: {0}".format(exc))

    return ma_path


def _snapshot_thumbnail(target_path: str,
                        focus=None, size: int = 128) -> None:
    """Best-effort ``playblast`` of the current viewport centred on
    ``focus`` (a list of nodes). Silently returns if playblast is
    unavailable (batch mode, no viewport, etc.) — the .ma file is
    still valid, just without a preview."""
    if cmds is None:
        return

    prev_sel = None
    if focus:
        try:
            prev_sel = cmds.ls(selection=True, long=True) or []
            cmds.select(focus, replace=True)
            cmds.viewFit(all=False)
        except Exception:
            prev_sel = None

    try:
        cur_frame = cmds.currentTime(query=True)
    except Exception:
        cur_frame = 1

    tmp_prefix = target_path + "_tmp"
    result = None
    try:
        result = cmds.playblast(
            format="image",
            filename=tmp_prefix,
            widthHeight=(size, size),
            showOrnaments=False,
            percent=100,
            frame=[cur_frame],
            viewer=False,
            forceOverwrite=True,
            compression="png",
            offScreen=True,
        )
    except Exception:
        pass

    # Locate the file playblast actually wrote (Maya appends
    # frame number + extension) and rename it to target_path.
    parent = os.path.dirname(target_path) or "."
    prefix = os.path.basename(tmp_prefix)
    for f in list(os.listdir(parent)):
        if f.startswith(prefix) and f.lower().endswith(".png"):
            src = os.path.join(parent, f)
            try:
                if os.path.isfile(target_path):
                    os.remove(target_path)
                os.rename(src, target_path)
                break
            except Exception:
                pass

    if prev_sel is not None:
        try:
            if prev_sel:
                cmds.select(prev_sel, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #

def import_hair_from_library(
    ma_path: str,
    group: Optional[str] = None,
) -> List[str]:
    """Import ``ma_path`` into the current scene under a unique
    namespace, then reparent the imported hair mesh transform(s)
    under ``group`` (or under ``HairGroup`` if group is None).
    Returns the list of new nodes."""
    if cmds is None:
        raise RuntimeError("import_hair_from_library requires Maya.")
    if not os.path.isfile(ma_path):
        raise RuntimeError(
            "ファイルが見つかりません: {0}".format(ma_path))

    base = os.path.basename(ma_path)[:-3]
    # Namespace must be unique per import. Suffix with an int if
    # the primary namespace already exists.
    ns = _unique_namespace("lib_" + _sanitize_name(base))

    try:
        new_nodes = cmds.file(
            ma_path,
            i=True,  # import (not reference)
            namespace=ns,
            ignoreVersion=True,
            returnNewNodes=True,
            renameAll=False,
            preserveReferences=True,
        ) or []
    except Exception as exc:
        raise RuntimeError("import 失敗: {0}".format(exc))

    # Move every strand mesh transform in the imported set under
    # the requested group (or under HairGroup by default).
    for n in list(new_nodes):
        if not cmds.objExists(n):
            continue
        if hair._is_hair_strand_transform(n):
            try:
                hair.move_strand_to_group(n, group)
            except Exception as exc:
                cmds.warning(
                    "[maya_hair_tool] インポート後の再ペアレント"
                    "失敗 ({0}): {1}".format(n, exc))

    return new_nodes


def _unique_namespace(base: str) -> str:
    """Return a namespace name based on ``base`` that isn't already
    in use — Maya's import fails if the namespace clashes."""
    if cmds is None:
        return base
    try:
        existing = set(cmds.namespaceInfo(
            listOnlyNamespaces=True, recurse=True) or [])
    except Exception:
        existing = set()
    if base not in existing:
        return base
    i = 2
    while "{0}_{1:02d}".format(base, i) in existing:
        i += 1
    return "{0}_{1:02d}".format(base, i)


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #

def delete_library_entry(name: str) -> None:
    """Remove both the ``.ma`` and its ``.png`` for the given preset
    name. Silently no-ops for missing files so double-clicks are
    harmless."""
    root = library_root()
    safe = _sanitize_name(name)
    if not safe:
        return
    for ext in (".ma", ".png"):
        p = os.path.join(root, safe + ext)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass
