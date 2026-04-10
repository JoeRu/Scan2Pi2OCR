import pytest
from app.ocr import is_blank_page


def test_is_blank_page_true():
    # black=3, white=1234 → ratio 0.0024 < 0.01 → blank
    histogram = "     1234: (255,255,255) #FFFFFF white\n      3: (0,0,0) #000000 black\n"
    assert is_blank_page(histogram) is True


def test_is_blank_page_false():
    # black=500, white=1000 → ratio 0.5 → not blank
    histogram = "     1000: (255,255,255) #FFFFFF white\n    500: (0,0,0) #000000 black\n"
    assert is_blank_page(histogram) is False


def test_is_blank_page_no_black():
    # no black pixels → blank
    histogram = "     9999: (255,255,255) #FFFFFF white\n"
    assert is_blank_page(histogram) is True
