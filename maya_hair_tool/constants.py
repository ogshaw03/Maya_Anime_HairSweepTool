"""Shared constants for the Maya Anime Hair Sweep Tool."""

# Profile presets exposed in the UI. The integer value maps to the
# sweepMeshCreator.profilePolyType enum values in Maya.
#   0 = Round, 1 = Square, 2 = Ribbon, 3 = Custom
#
# The names below are the ones we want to expose to the user; some are
# implemented as tweaked Round / Square / Custom profiles.
PROFILE_ROUND = "Round"
PROFILE_OVAL = "Oval"
PROFILE_FLAT = "Flat"
PROFILE_SHARP = "Sharp"
PROFILE_DIAMOND = "Diamond"
PROFILE_TEAR = "TearDrop"
PROFILE_CUSTOM = "Custom"

PROFILE_TYPES = [
    PROFILE_ROUND,
    PROFILE_OVAL,
    PROFILE_FLAT,
    PROFILE_SHARP,
    PROFILE_DIAMOND,
    PROFILE_TEAR,
    PROFILE_CUSTOM,
]

# Maps our exposed profile name to the sweepMeshCreator.profilePolyType enum.
PROFILE_POLY_TYPE = {
    PROFILE_ROUND: 0,
    PROFILE_OVAL: 0,
    PROFILE_FLAT: 1,
    PROFILE_SHARP: 1,
    PROFILE_DIAMOND: 1,
    PROFILE_TEAR: 3,
    PROFILE_CUSTOM: 3,
}

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

# Custom attribute added to the sweepMeshCreator node so the tool can
# recognise and re-open its own strands later.
TOOL_TAG_ATTR = "animeHairTool"
