"""Python, JavaScript, and TypeScript indexing support."""


def __getattr__(name: str):
    if name == "registry":
        from csegraph._core.languages.registry import registry

        return registry
    raise AttributeError(name)


__all__ = ["registry"]
