from .bbc import BBCRenderer
from .live import LiveRenderer
from .rolling import RollingRenderer


def make_renderer(name: str, config: dict, *, layout=None):
    name = str(name).strip().lower()

    if name == "bbc_live":
        return LiveRenderer(config, layout=layout)

    if name == "rolling":
        return RollingRenderer(config)

    if name == "bbc":
        return BBCRenderer(config)

    raise ValueError(
        f"Unknown overlay layout {name!r}. "
        "Available layouts: rolling, bbc, bbc_live"
    )
