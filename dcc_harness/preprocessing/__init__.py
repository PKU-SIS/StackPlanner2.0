"""Pluggable DCC inputs, document ingestion, and public agent task bundles.

This package does not execute an agent or define benchmark scoring.
"""

from .models import Asset, Sample

__all__ = ["Asset", "Sample"]
