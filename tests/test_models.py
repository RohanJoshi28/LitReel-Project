from litreel.models import Slide, SlideStyle


def test_slide_style_default_dict():
    defaults = SlideStyle.default_dict()
    assert defaults["text_color"] == "#FFFFFF"
    assert defaults["outline_color"] == "#000000"
    assert defaults["font_weight"] == "700"
    assert defaults["underline"] is False


def test_slide_style_dict_when_missing_values():
    style = SlideStyle(text_color="#ff0000", underline=True)
    data = style.to_dict()
    assert data["text_color"] == "#ff0000".upper()
    assert data["outline_color"] == "#000000"
    assert data["font_weight"] == "700"
    assert data["underline"] is True


def test_slide_style_property_returns_defaults_when_missing():
    slide = Slide(text="hi", concept_id=1, order_index=0)
    assert slide.style_dict == SlideStyle.default_dict()
