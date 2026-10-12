"""In-memory stock levels."""

from __future__ import annotations


class OutOfStockError(Exception):
    pass


class Inventory:
    def __init__(self) -> None:
        self._levels: dict[str, int] = {}

    def add(self, sku: str, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._levels[sku] = self._levels.get(sku, 0) + quantity

    def remove(self, sku: str, quantity: int) -> None:
        available = self._levels.get(sku, 0)
        if quantity > available:
            raise OutOfStockError(f"{sku}: requested {quantity}, available {available}")
        self._levels[sku] = available - quantity

    def level(self, sku: str) -> int:
        return self._levels.get(sku, 0)
