"""Maya Anime Hair Sweep Tool.

Curve + Sweep Mesh based anime/cel-look hair authoring tool for Maya.

Public entry point::

    import maya_hair_tool
    maya_hair_tool.show()
"""

__version__ = "0.3.14"


def show():
    """Open the Hair Builder window.

    Imported lazily so this package can be imported outside Maya (e.g.
    for linting or version inspection) without requiring ``maya.cmds``.
    """
    from . import ui
    return ui.show()
