from .contracts import PayloadFormatter, ScoreProvider
from .formatter import DefaultPayloadFormatter
from .metrics import StaticScoreProvider, process as risk_process
from .utils import caller, helper, sanitize_user_id


class UserService:
    def __init__(
        self,
        formatter: PayloadFormatter | None = None,
        scorer: ScoreProvider | None = None,
    ):
        self.formatter = formatter or DefaultPayloadFormatter()
        self.scorer = scorer or StaticScoreProvider()

    def run(self, user_id: str) -> dict:
        normalized_id = sanitize_user_id(user_id)
        status = helper()
        payload = {
            "user_id": normalized_id,
            "status": status,
            "score": caller() + self.scorer.score_for(normalized_id),
            "risk": risk_process(),
        }
        return self.formatter.format(payload)


def run(user_id: str) -> dict:
    return UserService().run(user_id)
