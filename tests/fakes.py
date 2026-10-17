"""Подмена коллекции Chroma для тестов хранилища."""


class FakeCollection:
    def __init__(self):
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, ids, documents, metadatas, embeddings):
        for id_, doc, meta in zip(ids, documents, metadatas, strict=True):
            self.rows[id_] = (doc, meta)

    def delete(self, ids):
        for id_ in ids:
            self.rows.pop(id_, None)

    def get(self, include, ids=None, where=None):
        items = [(i, d) for i, (d, m) in self.rows.items()
                 if (ids is None or i in ids) and (where is None or m["kind"] == where["kind"])]
        return {"ids": [i for i, _ in items], "documents": [d for _, d in items]}
