"""Capability Intelligence Engine: read-only discovery of local engineering capabilities."""

from devroute.discovery.inventory import build_inventory, detect_tool, has_test_framework
from devroute.discovery.models import Finding, Inventory, Status
from devroute.discovery.project import ProjectFacts, scan_project

__all__ = [
    "Finding",
    "Inventory",
    "ProjectFacts",
    "Status",
    "build_inventory",
    "detect_tool",
    "has_test_framework",
    "scan_project",
]
