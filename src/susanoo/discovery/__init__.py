"""Capability Intelligence Engine: read-only discovery of local engineering capabilities."""

from susanoo.discovery.inventory import build_inventory, detect_tool, has_test_framework
from susanoo.discovery.models import Finding, Inventory, Status
from susanoo.discovery.project import ProjectFacts, scan_project

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
