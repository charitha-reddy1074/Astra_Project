"""Local sentence-embedding model (BAAI/bge-base-en-v1.5).

Loaded lazily on first use so that importing this module (and therefore the
whole API) has no heavy side effect — the ~400 MB model is only loaded if
embeddings are actually requested.
"""

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("BAAI/bge-base-en-v1.5")
    return _model


def generate_embeddings(texts):
    if not texts:
        return []
    return _get_model().encode(texts, convert_to_numpy=True, show_progress_bar=False)
