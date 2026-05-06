def clean_text(text: str) -> str:
    return text.strip().lower()


def parse_score(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


class NormalizeStage:
    def transform(self, rows: list[dict]) -> list[dict]:
        transformed = []
        for row in rows:
            score = parse_score(row["score"])
            transformed.append(
                {
                    "id": row["id"],
                    "name": clean_text(row["name"]),
                    "score": score,
                    "is_passing": score >= 60,
                }
            )
        return transformed
