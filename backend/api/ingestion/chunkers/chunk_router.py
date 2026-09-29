from backend.api.ingestion.chunkers.generic_chunker import build_chunks


def route_chunk_builder(framework_json: dict) -> list[dict]:
    """Route any canonical JSON framework to the generic chunk builder."""
    return build_chunks(framework_json)
