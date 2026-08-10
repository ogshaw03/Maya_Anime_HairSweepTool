"""Shared constants for the Maya Anime Hair Sweep Tool."""

# Profile presets exposed in the UI. Maya 2023's sweepMeshCreator uses
# a two-level shape enum:
#   * ``sweepProfileType``  0=Regular Polygon, 1=Rounded Rectangle,
#                           2=Line, 3=Arc, 4=Wave, 5=Custom
#   * ``profilePolyType``   0=Convex, 1=Star  (only for Regular Polygon)
# Our named presets combine those two attrs + scaleProfile* tweaks.
PROFILE_CIRCLE = "Circle"
PROFILE_ELLIPSE = "Ellipse"
PROFILE_RIBBON = "Ribbon"
PROFILE_STAR = "Star"
PROFILE_RECTANGLE = "Rectangle"
PROFILE_ARC = "Arc"
PROFILE_WAVE = "Wave"
PROFILE_CUSTOM = "Custom"

PROFILE_TYPES = [
    PROFILE_CIRCLE,
    PROFILE_ELLIPSE,
    PROFILE_RIBBON,
    PROFILE_STAR,
    PROFILE_RECTANGLE,
    PROFILE_ARC,
    PROFILE_WAVE,
    PROFILE_CUSTOM,
]

# Default anime hair authoring values.
DEFAULT_THICKNESS = 1.0
DEFAULT_WIDTH = 1.0
DEFAULT_HEIGHT = 1.0
DEFAULT_ROOT_SCALE = 1.0
DEFAULT_MIDDLE_SCALE = 1.0
DEFAULT_TIP_SCALE = 0.05
DEFAULT_TWIST = 0.0
DEFAULT_ROTATION = 0.0
DEFAULT_SUBDIVISIONS_AXIS = 8
DEFAULT_SUBDIVISIONS_LENGTH = 12

# Node naming conventions.
HAIR_GROUP_NAME = "HairGroup"
HAIR_STRAND_PREFIX = "hair"
HAIR_CURVE_SUFFIX = "_curve"
HAIR_MESH_SUFFIX = "_mesh"
HAIR_SWEEP_SUFFIX = "_sweep"
HAIR_PROFILE_SUFFIX = "_profile"

# Custom attribute added to the sweepMeshCreator node so the tool can
# recognise and re-open its own strands later.
TOOL_TAG_ATTR = "animeHairTool"

# Scene-embedded (internal) library group. Sits at scene root as a
# sibling of HairGroup and stays hidden so preset strands don't
# clutter the viewport or the "毛束一覧" tree. Kept separate from
# HairGroup on purpose — every strand-enumeration helper in hair.py
# walks HairGroup's subtree, so preset strands stored under
# InLibrary are naturally filtered out.
INTERNAL_LIBRARY_GROUP = "InLibrary"

# Boolean attribute added to a sweepMeshCreator when its strand is
# copied into InLibrary. Marks the strand as "preset material" so
# tools can distinguish presets from live strands.
PRESET_TAG_ATTR = "hairLibraryPreset"

# Per-group display colour stored on the group transform as three
# float attrs (RGB in 0-1). Applied to each descendant strand's
# mesh transform via ``overrideEnabled`` + ``overrideColorRGB``
# — non-destructive to the actual material and toggleable via
# the group-colour visibility switch.
GROUP_COLOR_R_ATTR = "hairGroupColorR"
GROUP_COLOR_G_ATTR = "hairGroupColorG"
GROUP_COLOR_B_ATTR = "hairGroupColorB"
