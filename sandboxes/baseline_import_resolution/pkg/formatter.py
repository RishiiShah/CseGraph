class DefaultPayloadFormatter:
    def format(self, payload: dict) -> dict:
        return {
            "id": payload["user_id"],
            "status": payload["status"],
            "score": int(payload["score"]),
            "label": f"{payload['status']}-{payload['score']}",
            "risk": int(payload["risk"]),
        }


def format_payload(payload: dict) -> dict:
    return DefaultPayloadFormatter().format(payload)
