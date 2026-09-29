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
        #: Ordered live-model fallback. `GROQ_MODEL` may be pinned to something
        #: that has since been decommissioned; without a chain the SDK SDK-call
        #: 404s and the whole chat surface degrades until someone edits .env.
        self.model_chain = list(
            getattr(settings, "GROQ_MODEL_CHAIN", None) or [self.model]
        )
        # A blank/unset key must not 500: the service stays "installed but not
        # configured" and chat() degrades gracefully to the retrieved controls.
        self.client = (
            Groq(api_key=settings.GROQ_API_KEY, timeout=60.0, max_retries=2)
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

        response = None
        last_exc: Exception | None = None
        for attempt_model in self.model_chain:
            try:
                response = self.client.chat.completions.create(
                    model=attempt_model,
                    messages=messages,
                    temperature=0.2,
                    # gpt-oss spends part of the budget on reasoning tokens, so
                    # 900 truncated the answer mid-sentence often enough to
                    # surface as an empty completion.
                    max_tokens=4000,
                )
                break
            except Exception as exc:  # noqa: BLE001 - classified below
                last_exc = exc
                detail = str(exc)
                # 404 / 400 model errors are deterministic for this model —
                # fail straight over to the next one instead of retrying it.
                if "model_not_found" in detail or "decommissioned" in detail \
                        or "invalid_request_error" in detail or "not_found" in detail:
                    continue
                break

        if response is None:
            # Groq's free tier enforces a tokens-per-day cap, and a configured
            # model can be decommissioned or rejected outright. Letting either
            # bubble up turned a recoverable problem into a raw 500 in the UI,
            # so answer with the retrieved controls and an explanation instead.
            detail = str(last_exc or "")
            is_rate_limit = "rate_limit" in detail or "429" in detail
            is_bad_model = (
                "model_decommissioned" in detail
                or "decommissioned" in detail
                or "model_not_found" in detail
                or "invalid_request_error" in detail
            )
            wait = ""
            m = re.search(r"try again in ([0-9hms.]+)", detail)
            if m:
                wait = f" Try again in about {m.group(1)}."
            if is_rate_limit:
                note = (
                    "The AI model is temporarily unavailable because the Groq API "
                    f"token quota has been reached.{wait}\n\n"
                    "The relevant controls were still retrieved and are cited below."
                )
            elif is_bad_model:
                note = (
                    "The configured AI model (`{model}`) is not available on this "
                    "Groq account — it has been decommissioned. Set GROQ_MODEL to a "
                    "currently served model (for example `openai/gpt-oss-120b`) and "
                    "restart the backend.\n\n"
                    "The relevant controls were still retrieved and are cited below."
                ).format(model=self.model)
            else:
                note = (
                    "The AI model could not be reached for this request.\n\n"
                    "The relevant controls were still retrieved and are cited below."
                )
            if citations:
                note += "\n\n" + "\n".join(
                    f"• [{c.get('framework')}] {c.get('control_code')}"
                    for c in citations[:8]
                )
            return {"answer": note, "citations": citations, "rate_limited": True}

        answer = (response.choices[0].message.content or "").strip()
        if not answer:
            # A reasoning model can finish with an empty `content`. Returning
            # the retrieved controls beats raising AttributeError on None.
            answer = (
                "The model returned an empty response for this question.\n\n"
                "The relevant controls were still retrieved and are cited below."
            )
        return {"answer": answer, "citations": citations}
