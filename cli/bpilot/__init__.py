from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("bpilot-cli")
except PackageNotFoundError:
    __version__ = "0.0.1"
