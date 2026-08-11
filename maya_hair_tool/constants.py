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

# HairGroup is split into two fixed sub-groups so meshes and their
# guide curves stay tidy — HairGroup/Geometry_group/<name>/... for
# mesh transforms, HairGroup/Curve_group/<name>/... for the curves
# that drive them. Same <name> exists in both containers per
# user-created hair group.
GEOMETRY_GROUP_NAME = "Geometry_group"
CURVE_GROUP_NAME = "Curve_group"

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
# float attrs (RGB in 0-1). Applied to strands via a dedicated
# vertex-colour set (``GROUP_COLOR_SET``) + ``displayColors=1`` on
# the mesh shape. The mesh's actual shading assignment is never
# touched — the toggle just flips ``displayColors`` on/off so the
# user can freely edit the real material at any time.
#
# The v0.3.7 (drawing-override) and v0.3.8/9 (shader-swap)
# approaches are dropped: the former only recoloured wireframes,
# the latter interfered with material editing. The two obsolete
# constants (GROUP_COLOR_MATERIAL_PREFIX / ORIGINAL_SHADING_GROUP_
# ATTR) are kept for one release so the cleanup pass in
# hair.py can restore any strands users still have swapped.
GROUP_COLOR_R_ATTR = "hairGroupColorR"
GROUP_COLOR_G_ATTR = "hairGroupColorG"
GROUP_COLOR_B_ATTR = "hairGroupColorB"
GROUP_COLOR_SET = "hairGroupColorSet"
GROUP_COLOR_MATERIAL_PREFIX = "hairGroupMat_"   # legacy (v0.3.8/9)
ORIGINAL_SHADING_GROUP_ATTR = "hairOriginalSG"  # legacy (v0.3.8/9)


# --------------------------------------------------------------------------- #
# Phase 6 — Braid Generator
# --------------------------------------------------------------------------- #

# Naming convention for auto-created braid groups (Braid_01, Braid_02, ...).
BRAID_GROUP_PREFIX = "Braid_"

# Braid defaults — chosen so the FIRST GENERATION already looks like
# a braid without any manual tweaking. Key ratio: strand_thickness
# should be < half of braid_radius or the three woven strands touch
# and read as one lump. Also: sweepMeshCreator's default
# interpolationPrecision (12) is far too coarse for a helical curve
# with 20+ turns — braid strands use a much higher length subdiv
# (see BRAID_STRAND_SUBDIV_LENGTH) so the mesh follows the twist
# smoothly instead of appearing as a chain of angular blocks.
DEFAULT_BRAID_TURNS_PER_LENGTH = 0.5   # ~5 full turns over a 10-unit spine
DEFAULT_BRAID_RADIUS = 0.5             # offset from spine to each strand centre
DEFAULT_BRAID_STRAND_THICKNESS = 0.2   # per-strand mesh thickness (~40% of radius)
DEFAULT_BRAID_TIP_TAPER = 0.6          # 0=constant radius, 1=radius→0 at tip
# Depth ratio — how deep the over/under crossings sit relative to the
# braid's side-to-side width. Real hair braids are essentially flat
# with a shallow depth (0.3-0.5). 1.0 makes depth and width equal
# (chunky rope-looking braid); 0 collapses to a flat 2D zig-zag.
DEFAULT_BRAID_DEPTH_RATIO = 0.4
# Tail: fraction of the spine (from the tip end) reserved for the
# "un-braided" tail where the strands come out of the tie and hang
# as a ponytail-like tassel. Matches ma/test001.ma exactly (0.176)
# so first-generation output looks proportional to the reference
# silhouette. Higher values stretch the same shape multipliers
# over a longer Y range and read as a wispy elongated tail
# instead of a compact teardrop.
DEFAULT_BRAID_TAIL_LENGTH = 0.176
# Per-region density multipliers for the weave. 1.0 = uniform along
# the spine. Higher values in one region = more crossings there
# (tighter weave); lower = looser. Interpolated as a piecewise-
# linear function across (0, top), (0.5, middle), (1, bottom).
DEFAULT_BRAID_DENSITY_TOP = 1.0
DEFAULT_BRAID_DENSITY_MIDDLE = 1.0
DEFAULT_BRAID_DENSITY_BOTTOM = 1.0
DEFAULT_BRAID_SAMPLES = 32             # helix smoothness; 32 covers most spines
# Tail strands: after the tie the braid opens into N free hair
# strands (each its own curve + mesh + sweepMeshCreator) so users
# can individually adjust thickness / taper / colour via the
# normal hair sliders. Rebuild preserves per-strand tweaks when
# the count matches (in-place CV update); changing the count
# forces a full recreate.
DEFAULT_BRAID_TAIL_STRAND_COUNT = 6
DEFAULT_BRAID_TAIL_SAMPLES = 12       # samples along each tail strand curve
DEFAULT_BRAID_TAIL_THICKNESS = 0.132  # initial strand thickness (matches ma/test001.ma)
# Tail tip taper — writes to each tail sweepMeshCreator's
# ``taperCurve`` tip value (root and middle stay at 1.0). 0.0 =
# strand comes to a needle point at the tip, 1.0 = uniform
# thickness the whole way. Default 0.05 gives sharp hair-like tips.
DEFAULT_BRAID_TAIL_TIP_TAPER = 0.05

# sweepMeshCreator subdivisions used when the braid feeds curves
# through hair.create_hair_from_selected_curves. Overriding the
# global hair defaults (axis=8 / length=12) because those values
# produce a visibly angular mesh for anything more than a straight
# hair strand.
#   BRAID_STRAND_SUBDIV_AXIS   — cross-section polygon sides
#   BRAID_STRAND_SUBDIV_LENGTH — sweepMeshCreator.interpolationPrecision
#                                (samples along the strand curve; 12 is
#                                the built-in default and reads as a
#                                chain of blocks on a helical curve).
#   BRAID_STRAND_TIP_SCALE     — braid strand taper at the tie end
#                                (1.0 = uniform thickness through the
#                                woven section; the braid's overall
#                                pinch is expressed via the offset
#                                formula, not the sweep's taper ramp).
DEFAULT_BRAID_STRAND_SUBDIV_AXIS = 12
DEFAULT_BRAID_STRAND_SUBDIV_LENGTH = 80
DEFAULT_BRAID_STRAND_TIP_SCALE = 1.0
DEFAULT_BRAID_TAIL_SUBDIV_AXIS = 10
DEFAULT_BRAID_TAIL_SUBDIV_LENGTH = 30

# sweepMeshCreator interpolation ── mode override for braid meshes.
# The default mode 0 (Precision) uses ``interpolationPrecision`` as
# an adaptive quality threshold: values 1..74 all resolve to the
# same coarse mesh, and only >= 75 does the sampler actually add
# subdivisions. That dead zone makes the "長さ分割数" slider feel
# broken on helical curves.
#
# Switching braid meshes to mode 3 (Distance) uses
# ``interpolationDistance`` as a literal spacing along the curve —
# one cross-section every X units. Predictable and dense regardless
# of curvature. The "長さ分割数" slider (which writes to
# interpolationPrecision) becomes a no-op on braid strands as a
# result — that's an acceptable trade for a first-generation mesh
# that already reads as smooth without any manual tuning.
_INTERP_MODE_DISTANCE = 3
_INTERP_MODE_START_TO_END = 1
DEFAULT_BRAID_STRAND_INTERP_DISTANCE = 0.08   # dense (helix follows twist)
# Tail strands use Start-to-End mode with a fixed step count
# instead of a per-unit distance — tail curves are short (typically
# < 1 unit), so a distance-based sampler drops to only a handful
# of cross-sections and each strand renders as a chunky block.
# Fixed 30 steps guarantees the mesh reads as a smooth hair strand
# regardless of the tail's absolute length.
DEFAULT_BRAID_TAIL_INTERP_STEPS = 30
# Nyquist / aliasing floor: fewer than this many samples PER FULL TURN and
# the generated helix folds into a zig-zag instead of a smooth spiral.
# 8 samples/turn keeps the curve visually smooth at any reasonable
# tightness setting; the braid generator bumps ``num_samples`` up
# automatically when ``turns_per_length * spine_length`` demands it.
BRAID_SAMPLES_PER_TURN = 8
