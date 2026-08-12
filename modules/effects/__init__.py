"""Module 6: screen shake on laugh/scream peaks."""

__all__ = ["run"]


def __getattr__(name: str):
    if name == "run":
        from modules.effects.runner import run

        return run
    raise AttributeError(name)
