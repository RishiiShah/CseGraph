class HashEmbedder:
    def embed(self, chunks: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for chunk in chunks:
            token_count = max(len(chunk.split()), 1)
            char_count = max(len(chunk), 1)
            vectors.append([token_count / 20.0, char_count / 200.0])
        return vectors
