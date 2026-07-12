from src.service import create_user, display_name


def test_create_user_normalizes_name() -> None:
    assert create_user(" Ada ") == "user:ada"


def test_display_name_formats_name() -> None:
    assert display_name(" ada ") == "Ada"
