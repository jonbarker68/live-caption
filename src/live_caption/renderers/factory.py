from .bbc import BBCRenderer
from .rolling import RollingRenderer


def make_renderer(name: str, config: dict):
    name = str(name).strip().lower()

    if name == "rolling":
        return RollingRenderer(config)

    if name == "bbc":
        return BBCRenderer(config)

    raise ValueError(
        f"Unknown overlay layout {name!r}. "
        "Available layouts: rolling, bbc"
    )
