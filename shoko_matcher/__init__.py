"""Shoko filename matcher.

A small CLI/library that finds unrecognized files in a Shoko Server library and
links them to the correct episodes through the Shoko v3 REST API.
"""

__version__ = "0.1.0"

from .config import Config, ConfigError  # noqa: F401
from .client import ShokoClient, ShokoError, ShokoAuthError, ShokoNotFound  # noqa: F401
from .parser import parse_filename  # noqa: F401
