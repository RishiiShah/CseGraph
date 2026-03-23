class SentenceChunker:
    def chunk(self, text: str) -> list[str]:
        parts = [part.strip() for part in text.split(".") if part.strip()]
        return parts or [text.strip()]
