"""Подмена коллекции Chroma для тестов хранилища."""


class FakeCollection:
    def __init__(self):
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, ids, documents, metadatas, embeddings):
        for id_, doc, meta in zip(ids, documents, metadatas, strict=True):
            self.rows[id_] = (doc, meta)

    def get(self, where, include):
        items = [(i, d) for i, (d, m) in self.rows.items() if m["kind"] == where["kind"]]
        return {"ids": [i for i, _ in items], "documents": [d for _, d in items]}
