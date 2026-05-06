class JsonWriter:
    def write(self, rows: list[dict]) -> list[dict]:
        passing = [row for row in rows if row["is_passing"]]
        summary = {
            "total": len(rows),
            "passing": len(passing),
            "avg_score": round(sum(row["score"] for row in rows) / len(rows), 2)
            if rows
            else 0,
        }
        return [{"summary": summary, "records": rows}]
