"""Module 7: flourish / stylized keyword overlays."""

__all__ = ["run"]


def __getattr__(name: str):
    if name == "run":
        from modules.flourish.runner import run

        return run
    raise AttributeError(name)
