import pytest

from drik.model import LocalizationError, ModelClient, VQAError


@pytest.fixture
def client():
    c = ModelClient(endpoint="http://localhost:1234/v1")
    yield c
    c.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"x": 500, "y": 250}', (500, 250)),
        ('here you go: {"x":100,"y":900} done', (100, 900)),
        ('```json\n{"x": 0, "y": 1000}\n```', (0, 1000)),
        ("(640, 480)", (640, 480)),
    ],
)
def test_parse_coords(client, raw, expected):
    assert client._parse_coords(raw) == (float(expected[0]), float(expected[1]))


def test_parse_coords_failure(client):
    with pytest.raises(LocalizationError):
        client._parse_coords("I cannot determine the location.")


def test_to_pixels_normalized(client):
    # 500/1000 of a 1280x800 viewport -> center-ish.
    assert client._to_pixels(500, 500, viewport=(1280, 800)) == (640, 400)
    # Clamped into range.
    assert client._to_pixels(1000, 1000, viewport=(1280, 800)) == (1279, 799)


def test_to_pixels_pixel_space():
    c = ModelClient(coord_space="pixel")
    assert c._to_pixels(300, 200, viewport=(1280, 800)) == (300, 200)
    c.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("yes", True),
        ("Yes.", True),
        ("no", False),
        ("No, it is not visible.", False),
        ("YES", True),
    ],
)
def test_parse_yesno(client, raw, expected):
    assert client._parse_yesno(raw) is expected


def test_parse_yesno_failure(client):
    with pytest.raises(VQAError):
        client._parse_yesno("maybe, hard to say")
