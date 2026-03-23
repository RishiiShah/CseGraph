class CSVLoader:
    def load(self, raw_rows: list[str]) -> list[dict]:
        rows: list[dict] = []
        for row in raw_rows:
            parts = [part.strip() for part in row.split(",")]
            if len(parts) != 3:
                continue
            user_id, name, score = parts
            rows.append({"id": user_id, "name": name, "score": score})
        return rows
