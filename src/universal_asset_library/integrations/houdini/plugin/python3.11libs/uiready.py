"""Start the ShotBox Assets bridge after the Houdini 21 UI is ready."""

try:
    import hou
    from ual_houdini_bridge import start

    if hou.isUIAvailable():
        start()
except Exception as error:
    print(f"ShotBox Assets Houdini Bridge did not start: {error}")
