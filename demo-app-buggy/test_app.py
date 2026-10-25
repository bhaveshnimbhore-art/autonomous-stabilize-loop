from app import total
def test_total():
    assert total(100, 2, 0.10) == 220
def test_no_tax():
    assert total(50, 1, 0) == 50


def test_zero_qty():
    assert total(100, 0, 0.10) == 0
