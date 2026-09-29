from backend.api.embeddings.chroma_loader import get_or_create_collection
from backend.api.embeddings.embedder import generate_embeddings


def retrieve_chunks(query: str, collection_name: str, top_k: int = 5) -> dict:
    """Query a single named Chroma collection and return raw results."""
    collection = get_or_create_collection(collection_name)
    embedding = generate_embeddings([query])[0].tolist()
    return collection.query(query_embeddings=[embedding], n_results=top_k)


def retrieve_all_frameworks(
    query: str,
    top_k: int = 4,
    collection_names: list[str] | None = None,
) -> dict:
    """
    Query all provided collection names and merge results.
    Returns {"documents": [[...]], "metadatas": [[...]]}.
    """
    if not collection_names:
        return {"documents": [[]], "metadatas": [[]]}

    all_docs: list[str] = []
    all_meta: list[dict] = []

    for name in collection_names:
        try:
            results = retrieve_chunks(query, name, top_k=top_k)
            all_docs.extend(results.get("documents", [[]])[0])
            all_meta.extend(results.get("metadatas", [[]])[0])
        except Exception as e:
            print(f"Retrieval skipped for '{name}': {e}")

    return {"documents": [all_docs], "metadatas": [all_meta]}
