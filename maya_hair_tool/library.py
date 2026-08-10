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
from . import duplicate
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


def _find_model_panel() -> Optional[str]:
    """Return the active model panel (or the first available one).
    Playblast needs a model panel to render from; batch mode / no
    open viewport → None."""
    if cmds is None:
        return None
    try:
        p = cmds.getPanel(withFocus=True)
        if p and cmds.getPanel(typeOf=p) == "modelPanel":
            return p
    except Exception:
        pass
    try:
        panels = cmds.getPanel(type="modelPanel") or []
        for p in panels:
            if cmds.modelPanel(p, exists=True):
                return p
    except Exception:
        pass
    return None


def _snapshot_thumbnail(target_path: str,
                        focus=None, size: int = 128) -> bool:
    """Playblast a single frame showing ONLY ``focus`` and save the
    result to ``target_path`` (must end in ``.png``). Returns True
    on success, False on any failure.

    Uses ``cmds.playblast(completeFilename=…)`` so Maya writes the
    exact filename we want — no frame-number suffix to hunt for
    afterwards. Falls back to the ``filename``+prefix-search
    pattern when completeFilename returns without producing the
    file (some Maya 2023 builds behave that way).

    Isolate flow uses toggle-off/on to clear any prior isolate list,
    then adds just the focus objects, so the snapshot contains
    ONLY the target strand — no other scene geometry.
    """
    if cmds is None:
        print("[maya_hair_tool] サムネ生成: cmds が無い (batch mode?)")
        return False

    panel = _find_model_panel()
    if not panel:
        cmds.warning(
            "[maya_hair_tool] サムネ生成: model panel が見つかりません "
            "(viewport が閉じている / batch mode)")
        return False

    prev_sel = cmds.ls(selection=True, long=True) or []
    prev_iso_state = None
    try:
        prev_iso_state = cmds.isolateSelect(
            panel, query=True, state=True)
    except Exception:
        prev_iso_state = None

    target_maya = target_path.replace("\\", "/")
    ok = False
    try:
        # Isolate: toggle off → on to reset the isolate list,
        # then re-add our focus so the panel shows ONLY it.
        if focus:
            try:
                cmds.select(focus, replace=True)
                cmds.isolateSelect(panel, state=0)
                cmds.isolateSelect(panel, state=1)
                cmds.select(focus, replace=True)
                cmds.isolateSelect(panel, addSelected=True)
                cmds.viewFit(panel, all=False)
            except Exception as exc:
                cmds.warning(
                    "[maya_hair_tool] isolate 失敗 (続行): "
                    "{0}".format(exc))

        try:
            cur_frame = cmds.currentTime(query=True)
        except Exception:
            cur_frame = 1

        # Preferred path — completeFilename writes to exactly the
        # given path (no frame padding appended). Available in Maya
        # 2016+ so guaranteed on 2023.
        result = None
        try:
            result = cmds.playblast(
                format="image",
                completeFilename=target_maya,
                widthHeight=[size, size],
                percent=100,
                frame=[cur_frame],
                viewer=False,
                forceOverwrite=True,
                showOrnaments=False,
            )
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] playblast (completeFilename) "
                "例外: {0}".format(exc))
            result = None

        if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
            ok = True
        else:
            # Fallback: use ``filename`` + suffix hunt.
            target_dir = os.path.dirname(target_path) or "."
            base_no_ext = os.path.splitext(target_path)[0]
            tmp_base = base_no_ext + "__thumb_tmp"
            tmp_basename = os.path.basename(tmp_base)
            try:
                before = set(f for f in os.listdir(target_dir)
                             if f.startswith(tmp_basename))
            except Exception:
                before = set()
            try:
                cmds.playblast(
                    format="image",
                    filename=tmp_base.replace("\\", "/"),
                    widthHeight=[size, size],
                    percent=100,
                    frame=[cur_frame],
                    viewer=False,
                    forceOverwrite=True,
                    compression="png",
                    showOrnaments=False,
                    framePadding=1,
                )
            except Exception as exc:
                cmds.warning(
                    "[maya_hair_tool] playblast (filename) "
                    "例外: {0}".format(exc))
            try:
                after = set(f for f in os.listdir(target_dir)
                            if f.startswith(tmp_basename))
            except Exception:
                after = set()
            produced = sorted(after - before) or sorted(after)
            for f in produced:
                src = os.path.join(target_dir, f)
                try:
                    if os.path.isfile(target_path):
                        os.remove(target_path)
                    os.rename(src, target_path)
                    ok = True
                    break
                except Exception:
                    continue
            for f in list(os.listdir(target_dir)):
                if f.startswith(tmp_basename):
                    try:
                        os.remove(os.path.join(target_dir, f))
                    except Exception:
                        pass

        if not ok:
            cmds.warning(
                "[maya_hair_tool] サムネイル未生成: "
                "target={0} (result={1!r}). Script Editor で "
                "playblast の詳細エラーを確認してください。".format(
                    target_path, result))
        else:
            print("[maya_hair_tool] サムネ保存: {0}".format(target_path))
    finally:
        try:
            if prev_iso_state is not None:
                cmds.isolateSelect(panel, state=int(prev_iso_state))
        except Exception:
            pass
        try:
            if prev_sel:
                cmds.select(prev_sel, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass
    return ok


def regenerate_thumbnail(name: str, size: int = 128) -> bool:
    """Re-generate the .png thumbnail for an already-saved external
    preset. Loads the .ma temporarily is out of scope for MVP —
    this variant just plays back the current scene's version of a
    strand if one exists, otherwise the thumbnail stays blank."""
    root = library_root()
    safe = _sanitize_name(name)
    ma_path = os.path.join(root, safe + ".ma")
    png_path = os.path.join(root, safe + ".png")
    if not os.path.isfile(ma_path):
        return False
    # For now regenerate only from the current selection — user is
    # expected to select the strand they want a fresh thumbnail
    # for and then invoke this. Full "load .ma into temp, snapshot,
    # discard" round-trip can come later.
    _snapshot_thumbnail(png_path, focus=cmds.ls(selection=True) or None,
                        size=size)
    return os.path.isfile(png_path)


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

# --------------------------------------------------------------------------- #
# Internal (scene-embedded) library
# --------------------------------------------------------------------------- #

def ensure_internal_library_group() -> str:
    """Return the InLibrary transform (creating it hidden at scene
    root on first call). Placed as a *sibling* of HairGroup rather
    than a child, so every strand-enumeration helper in hair.py
    (which walks HairGroup's subtree) naturally excludes preset
    strands stored here."""
    if cmds is None:
        raise RuntimeError("requires Maya")
    if cmds.objExists(C.INTERNAL_LIBRARY_GROUP):
        return C.INTERNAL_LIBRARY_GROUP
    grp = cmds.group(
        empty=True, name=C.INTERNAL_LIBRARY_GROUP, world=True)
    try:
        cmds.setAttr(grp + ".visibility", 0)
    except Exception:
        pass
    return grp


def _tag_as_preset(creator: str) -> None:
    """Add / lock the ``hairLibraryPreset`` bool attribute on the
    sweepMeshCreator so we can distinguish presets from live
    strands programmatically."""
    if not cmds.attributeQuery(
            C.PRESET_TAG_ATTR, node=creator, exists=True):
        try:
            cmds.addAttr(
                creator, longName=C.PRESET_TAG_ATTR,
                attributeType="bool", defaultValue=True)
            cmds.setAttr(
                creator + "." + C.PRESET_TAG_ATTR, True, lock=True)
        except Exception:
            pass


def _untag_preset(creator: str) -> None:
    """Remove the preset tag — used when a preset is imported back
    out of InLibrary and becomes a normal live strand."""
    if cmds.attributeQuery(
            C.PRESET_TAG_ATTR, node=creator, exists=True):
        try:
            cmds.setAttr(
                creator + "." + C.PRESET_TAG_ATTR, lock=False)
            cmds.deleteAttr(creator + "." + C.PRESET_TAG_ATTR)
        except Exception:
            pass


def _move_to_internal_library(mesh_xform: str) -> str:
    """Reparent a strand mesh transform (and its guide curve) under
    the InLibrary group. Returns the mesh transform's new full path.
    """
    library_grp = ensure_internal_library_group()
    creators = su.sweep_creators_from_nodes([mesh_xform])
    creator = creators[0] if creators else None

    # Reparent the mesh transform.
    try:
        result = cmds.parent(mesh_xform, library_grp) or []
        if result:
            mesh_xform = result[0]
    except RuntimeError:
        pass

    # Reparent the associated guide curve so it lives alongside the
    # preset (keeps the whole strand self-contained within
    # InLibrary — deleting the group cleans everything up).
    if creator:
        curve = su.curve_from_creator(creator)
        if curve and cmds.objExists(curve):
            parents = cmds.listRelatives(
                curve, parent=True, fullPath=True) or []
            parent_short = (
                parents[0].split("|")[-1] if parents else "")
            if parent_short != C.INTERNAL_LIBRARY_GROUP:
                try:
                    cmds.parent(curve, library_grp)
                except RuntimeError:
                    pass
        _tag_as_preset(creator)
    return mesh_xform


def internal_thumbs_dir() -> str:
    """Directory that holds internal-preset thumbnails, keyed by
    the preset mesh's Maya UUID. Nested under the external library
    root so both live in the same overall folder."""
    d = os.path.join(library_root(), "_internal_thumbs")
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            pass
    return d


def internal_thumb_path(preset_mesh: str) -> Optional[str]:
    """Return the thumbnail path for a specific internal preset.
    Keyed by the mesh transform's Maya UUID so renames + scene
    reloads don't break the association. Returns None if UUID
    can't be resolved (very rare)."""
    if cmds is None or not cmds.objExists(preset_mesh):
        return None
    try:
        uuids = cmds.ls(preset_mesh, uuid=True) or []
    except Exception:
        return None
    if not uuids:
        return None
    # Sanitise defensively — Maya UUIDs are alphanumeric + hyphens
    # only, but a stray colon would break Windows paths.
    safe_uuid = re.sub(r"[^A-Za-z0-9_-]", "_", uuids[0])
    return os.path.join(internal_thumbs_dir(), safe_uuid + ".png")


def regenerate_internal_thumbnail(preset_mesh: str,
                                    focus: Optional[list] = None) -> bool:
    """Re-generate an internal preset's thumbnail. If ``focus`` is
    given it's used as the snapshot subject (the user can point at
    a different strand to serve as the visual). Otherwise falls
    back to the preset mesh itself."""
    thumb = internal_thumb_path(preset_mesh)
    if not thumb:
        return False
    if focus is None:
        focus = [preset_mesh]
    return _snapshot_thumbnail(thumb, focus=focus)


def save_hair_to_internal(
    name: Optional[str] = None,
    creator: Optional[str] = None,
    thumbnail: bool = True,
) -> str:
    """Duplicate the currently-selected strand (or ``creator``)
    into the InLibrary group as a reusable preset. Returns the
    preset's mesh transform path.

    Uses ``duplicate.duplicate_hair`` under the hood so the whole
    strand — scalar attrs, taperCurve ramp, custom profile curve —
    is faithfully copied. The copy is then reparented under
    InLibrary and tagged with ``hairLibraryPreset``."""
    if cmds is None:
        raise RuntimeError("save_hair_to_internal requires Maya.")

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

    new_creators = duplicate.duplicate_hair(
        [creator], count=1, offset=(0.0, 0.0, 0.0))
    if not new_creators:
        raise RuntimeError("プリセットの複製に失敗しました。")
    new_creator = new_creators[0]
    new_mesh = su.mesh_from_creator(new_creator)
    if not new_mesh:
        raise RuntimeError("複製後のメッシュが見つかりません。")

    new_mesh = _move_to_internal_library(new_mesh)

    # Optional rename so the preset shows up with the user's chosen
    # label in the internal library grid.
    safe = _sanitize_name(name) if name else ""
    if safe:
        try:
            new_mesh = cmds.rename(
                new_mesh, safe + "_preset_mesh")
        except Exception:
            pass

    # UUID-keyed thumbnail alongside the preset. Best-effort — if
    # playblast fails the .ma is still saved and the icon just
    # falls back to the generic Maya icon.
    if thumbnail:
        thumb = internal_thumb_path(new_mesh)
        if thumb:
            try:
                _snapshot_thumbnail(thumb, focus=[new_mesh])
            except Exception as exc:
                cmds.warning(
                    "[maya_hair_tool] 内部プリセット サムネ生成失敗: "
                    "{0}".format(exc))

    return new_mesh


def list_internal_library_entries() -> List[Tuple[str, str, str]]:
    """Return ``[(display_name, mesh_transform_path, thumb_path)]``
    for every preset stored inside the InLibrary group.
    ``thumb_path`` is an empty string when the UUID-keyed
    thumbnail file doesn't exist yet."""
    if cmds is None:
        return []
    if not cmds.objExists(C.INTERNAL_LIBRARY_GROUP):
        return []
    children = cmds.listRelatives(
        C.INTERNAL_LIBRARY_GROUP, children=True, type="transform",
        fullPath=True) or []
    entries = []
    for c in children:
        if not hair._is_hair_strand_transform(c):
            continue
        short = c.split("|")[-1]
        display = short
        for suf in ("_preset_mesh", "_mesh"):
            if display.endswith(suf):
                display = display[: -len(suf)]
                break
        thumb = internal_thumb_path(c) or ""
        if thumb and not os.path.isfile(thumb):
            thumb = ""
        entries.append((display, c, thumb))
    entries.sort(key=lambda pv: pv[0].lower())
    return entries


def import_from_internal(preset_mesh: str) -> Optional[str]:
    """Duplicate a preset back out of InLibrary into HairGroup so
    the user can adjust it as a live strand. Preset stays in place
    so the same slot can be reused."""
    if cmds is None:
        raise RuntimeError("import_from_internal requires Maya.")
    if not cmds.objExists(preset_mesh):
        raise RuntimeError(
            "プリセットが見つかりません: {0}".format(preset_mesh))

    creators = su.sweep_creators_from_nodes([preset_mesh])
    if not creators:
        raise RuntimeError(
            "プリセットの sweepMeshCreator が見つかりません。")
    creator = creators[0]

    new_creators = duplicate.duplicate_hair(
        [creator], count=1, offset=(0.0, 0.0, 0.0))
    if not new_creators:
        raise RuntimeError("プリセットの展開に失敗しました。")
    new_creator = new_creators[0]
    new_mesh = su.mesh_from_creator(new_creator)
    new_curve = su.curve_from_creator(new_creator)

    # Move to HairGroup and drop the preset tag on the copy so it
    # behaves like a normal strand from here on out.
    hair_grp = hair._ensure_hair_group()
    if new_mesh:
        try:
            result = cmds.parent(new_mesh, hair_grp) or []
            if result:
                new_mesh = result[0]
        except RuntimeError:
            pass
    if new_curve:
        parents = cmds.listRelatives(
            new_curve, parent=True, fullPath=True) or []
        parent_short = parents[0].split("|")[-1] if parents else ""
        if parent_short != C.HAIR_GROUP_NAME:
            try:
                cmds.parent(new_curve, hair_grp)
            except RuntimeError:
                pass
    _untag_preset(new_creator)
    if new_mesh:
        try:
            cmds.select(new_mesh, replace=True)
        except Exception:
            pass
    return new_mesh


def delete_internal_library_entry(preset_mesh: str) -> None:
    """Remove a preset from InLibrary along with its guide curve
    and its UUID-keyed thumbnail file. Silently ignores missing
    nodes so double-clicks are harmless."""
    if cmds is None:
        return
    if not cmds.objExists(preset_mesh):
        return
    # Grab thumbnail path BEFORE we delete the node (afterwards
    # the UUID lookup would fail).
    thumb = internal_thumb_path(preset_mesh)
    creators = su.sweep_creators_from_nodes([preset_mesh])
    to_delete = [preset_mesh]
    for c in creators:
        curve = su.curve_from_creator(c)
        if curve and cmds.objExists(curve):
            to_delete.append(curve)
    for n in to_delete:
        if cmds.objExists(n):
            try:
                cmds.delete(n)
            except Exception:
                pass
    if thumb and os.path.isfile(thumb):
        try:
            os.remove(thumb)
        except Exception:
            pass


def save_external_to_internal(ma_path: str) -> List[str]:
    """Import an external ``.ma`` preset straight into the InLibrary
    group and tag every landed strand as a preset. Returns the list
    of new node paths."""
    if cmds is None:
        raise RuntimeError("save_external_to_internal requires Maya.")
    if not os.path.isfile(ma_path):
        raise RuntimeError(
            "ファイルが見つかりません: {0}".format(ma_path))
    ensure_internal_library_group()

    # Reuse the existing external-import machinery; ``group=`` is
    # ignored for InLibrary (that group already exists at scene
    # root, not under HairGroup, so pass None and reparent
    # manually below).
    base = os.path.basename(ma_path)[:-3]
    ns = _unique_namespace("libpreset_" + _sanitize_name(base))
    try:
        new_nodes = cmds.file(
            ma_path, i=True, namespace=ns,
            ignoreVersion=True, returnNewNodes=True,
            renameAll=False, preserveReferences=True) or []
    except Exception as exc:
        raise RuntimeError("import 失敗: {0}".format(exc))

    landed_strands = []
    for n in list(new_nodes):
        if not cmds.objExists(n):
            continue
        if hair._is_hair_strand_transform(n):
            try:
                landed = _move_to_internal_library(n)
                landed_strands.append(landed)
            except Exception as exc:
                cmds.warning(
                    "[maya_hair_tool] 内部ライブラリへの移動失敗 "
                    "({0}): {1}".format(n, exc))
    # Generate thumbnails for the fresh presets.
    for strand in landed_strands:
        thumb = internal_thumb_path(strand)
        if thumb:
            try:
                _snapshot_thumbnail(thumb, focus=[strand])
            except Exception:
                pass
    return new_nodes


# --------------------------------------------------------------------------- #
# Existing external-library delete
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
