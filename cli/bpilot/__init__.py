from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("bpilot-cli")
except PackageNotFoundError:
    __version__ = "1.0.0"
