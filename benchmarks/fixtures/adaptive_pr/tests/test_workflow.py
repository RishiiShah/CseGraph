from src.workflow import run_signup


def test_signup_workflow() -> None:
    assert run_signup(" Ada ") == ("user:ada", "Ada")
