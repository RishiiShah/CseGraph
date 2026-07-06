class User:
    def __init__(self, name: str) -> None:
        self.name = name

    def save(self) -> str:
        return f"user:{self.name}"


class Order:
    def __init__(self, number: str) -> None:
        self.number = number

    def save(self) -> str:
        return f"order:{self.number}"
