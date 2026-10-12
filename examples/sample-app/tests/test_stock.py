import pytest

from inventory_app.stock import Inventory, OutOfStockError


def test_add_and_remove():
    inv = Inventory()
    inv.add("apple", 5)
    inv.remove("apple", 2)
    assert inv.level("apple") == 3


def test_cannot_remove_more_than_available():
    inv = Inventory()
    inv.add("pear", 1)
    with pytest.raises(OutOfStockError):
        inv.remove("pear", 2)


def test_quantity_must_be_positive():
    with pytest.raises(ValueError):
        Inventory().add("plum", 0)
