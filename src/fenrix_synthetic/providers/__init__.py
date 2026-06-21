"""Optional external provider adapters."""

from .nvidia_client import NVIDIAClient
from .nvidia_review import NVIDIAReviewAdapter

__all__ = ["NVIDIAClient", "NVIDIAReviewAdapter"]
