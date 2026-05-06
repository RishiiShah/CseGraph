class Pipeline:
    def __init__(self, loader, transformer, writer):
        self.loader = loader
        self.transformer = transformer
        self.writer = writer

    def run(self, raw_rows: list[str]) -> list[dict]:
        rows = self.loader.load(raw_rows)
        transformed = self.transformer.transform(rows)
        return self.writer.write(transformed)
