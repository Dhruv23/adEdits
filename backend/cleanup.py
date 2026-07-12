from __future__ import annotations

import shutil

from config import CONFIG


def purge_temp_workspace() -> None:
    """Delete all intermediate chunks/pieces/frames from temp_workspace once
    the final MP4 has rendered successfully, per PLAN.md Phase 6. The
    workspace directory itself (and .gitkeep) is preserved."""
    workspace = CONFIG.temp_workspace_dir
    if not workspace.exists():
        return

    for item in workspace.iterdir():
        if item.name == ".gitkeep":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
