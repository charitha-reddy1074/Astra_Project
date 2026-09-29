import re

from groq import Groq
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.config import settings
from backend.api.repositories.framework_repo import FrameworkRepository
from backend.api.retrieval.retriever import retrieve_all_frameworks
from backend.api.services.platform_catalog import build_platform_context


from backend.api.embeddings.chroma_loader import collection_name_for_framework


def _collection_name(framework_code: str) -> str:
    """Must match the name ingestion wrote to — see collection_name_for_framework.

    Previously a local copy of the slug logic, which produced invalid names for
    codes containing '/' or ':' (e.g. "ISO/IEC_27001:2022"). Chroma rejected
    those, retrieval was silently skipped, and the framework's controls were
    never citable.
    """
    return collection_name_for_framework(framework_code)


class AiService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.model = settings.GROQ_MODEL
        # A blank/unset key must not 500: the service stays "installed but not
        # configured" and chat() degrades gracefully to the retrieved controls.
        self.client = (
            Groq(api_key=settings.GROQ_API_KEY, timeout=30.0, max_retries=2)
            if settings.GROQ_API_KEY
            else None
        )

    async def chat(self, message: str, history: list[dict] | None = None) -> dict:
        fw_repo = FrameworkRepository(self.db)
        frameworks = await fw_repo.get_all()
        collection_names = [_collection_name(fw.code) for fw in frameworks]

        results = retrieve_all_frameworks(message, top_k=4, collection_names=collection_names)
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        citations = []
        context_parts = []
        for i, (doc, meta) in enumerate(zip(docs, metas)):
            fw = meta.get("framework_code") or meta.get("framework", "Unknown")
            ctrl = meta.get("control_code", "")
            domain = meta.get("domain", "")
            context_parts.append(f"[{i+1}] ({fw} / {ctrl}): {doc}")
            citations.append({
                "index": i + 1,
                "framework": fw,
                "control_code": ctrl,
                "domain": domain,
                "excerpt": doc[:200],
            })

        context = "\n\n".join(context_parts) if context_parts else "No relevant controls found."
        platform_context = build_platform_context(message)

        system_prompt = (
            "You are CyberAI, an expert cybersecurity assessment assistant for this "
            "compliance platform.\n"
            "You receive two context blocks:\n"
            "1. PLATFORM CATALOG — authoritative data about the frameworks loaded in "
            "this platform: their domains in order, and the expected evidence / "
            "required documents per domain. Treat this as ground truth for questions "
            "about domains, domain order, required evidence, or documents.\n"
            "2. RETRIEVED CONTROL EXCERPTS — vector-search results from framework "
            "control text. Use these for control-specific detail and cite them as "
            "[1], [2] etc.\n"
            "If neither block fully covers the question, answer from your general "
            "cybersecurity expertise and note briefly that it is general guidance "
            "rather than platform data. Never reply with only 'I don't have enough "
            "information' — always give the most helpful, accurate answer you can."
        )

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-6:])
        messages.append({
            "role": "user",
            "content": (
                f"PLATFORM CATALOG:\n{platform_context}\n\n"
                f"RETRIEVED CONTROL EXCERPTS:\n{context}\n\n"
                f"Question: {message}"
            ),
        })

        if self.client is None:
            note = (
                "The AI model is not configured on this platform (GROQ_API_KEY "
                "is not set), so no model call was made. The relevant controls "
                "were still retrieved and are cited below."
            )
            if citations:
                note += "\n\n" + "\n".join(
                    f"• [{c.get('framework')}] {c.get('control_code')}"
                    for c in citations[:8]
                )
            return {"answer": note, "citations": citations, "rate_limited": True}

        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=0.2, max_tokens=900
            )
        except Exception as exc:
            # Groq's free tier enforces a tokens-per-day cap. Letting that bubble
            # up turned a recoverable quota problem into a raw 500 in the UI, so
            # answer with the retrieved controls and an explanation instead.
            detail = str(exc)
            is_rate_limit = "rate_limit" in detail or "429" in detail
            if not is_rate_limit:
                raise
            wait = ""
            m = re.search(r"try again in ([0-9hms.]+)", detail)
            if m:
                wait = f" Try again in about {m.group(1)}."
            note = (
                "The AI model is temporarily unavailable because the Groq API "
                f"token quota has been reached.{wait}\n\n"
                "The relevant controls were still retrieved and are cited below."
            )
            if citations:
                note += "\n\n" + "\n".join(
                    f"• [{c.get('framework')}] {c.get('control_code')}"
                    for c in citations[:8]
                )
            return {"answer": note, "citations": citations, "rate_limited": True}

        answer = response.choices[0].message.content.strip()
        return {"answer": answer, "citations": citations}
