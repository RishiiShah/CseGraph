from src.router import dispatch
from src.service import display_name


def run_signup(name: str) -> tuple[str, str]:
    stored = dispatch("user", {"name": name})
    return stored, display_name(name)
