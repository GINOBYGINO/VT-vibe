"""Module 8: opening hook."""

__all__ = ["run"]


def __getattr__(name: str):
    if name == "run":
        from modules.hook.runner import run

        return run
    raise AttributeError(name)
