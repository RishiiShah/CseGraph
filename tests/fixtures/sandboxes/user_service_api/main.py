from services.user_service import create_user, get_user, get_user_summary


def run_demo() -> dict:
    create_user("u-1", "alice")
    create_user("u-2", "bob")
    return {
        "selected": get_user("u-1"),
        "summary": get_user_summary(),
    }
