class StaticScoreProvider:
    def score_for(self, user_id: str) -> int:
        return max(len(user_id), 1) * 10


def process() -> int:
    return 2
