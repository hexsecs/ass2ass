"""ASS2ASS: Agentic Signaling System to Any Shared Surface."""
from .protocol import Message, ProtocolError, Surface
from .filesystem import DirectorySurface

__all__ = ["Message", "ProtocolError", "Surface", "DirectorySurface"]
__version__ = "0.1.0"
