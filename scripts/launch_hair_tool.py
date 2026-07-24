"""Shelf/launcher entry point for the Anime Hair Sweep Tool.

Drop this file's parent folder into ``MAYA_SCRIPT_PATH`` (or place the
``maya_hair_tool`` package on ``sys.path``), then run the following two
lines from the Maya script editor or attach them to a shelf button::

    from maya_hair_tool import ui
    ui.show()
"""

from maya_hair_tool import ui


def main():
    ui.show()


if __name__ == "__main__":
    main()
