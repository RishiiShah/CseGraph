class TopKRetriever:
    def top_k(
        self,
        chunks: list[str],
        vectors: list[list[float]],
        query: str,
        k: int,
    ) -> list[str]:
        query_terms = {term.lower() for term in query.split()}
        scored = []
        for chunk, vector in zip(chunks, vectors):
            term_overlap = sum(1 for token in chunk.lower().split() if token in query_terms)
            score = term_overlap + vector[0] + vector[1]
            scored.append((score, chunk))

        scored.sort(reverse=True, key=lambda item: item[0])
        return [chunk for _, chunk in scored[:k]]
