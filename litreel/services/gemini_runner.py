from __future__ import annotations

from pathlib import Path
from typing import Sequence

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .pdf_parser import extract_text_from_document


class SlideConcept(BaseModel):
    name: str = Field(..., description="Short hook for a slideshow concept")
    description: str = Field(..., description="1-2 sentence description of the concept")
    slides: list[str] = Field(..., description="Ordered list of slide text")


class BookConcepts(BaseModel):
    concepts: list[SlideConcept]


class GeminiSlideshowGenerator:
    def __init__(
        self,
        api_key: str | None,
        model_name: str,
        document_parser=extract_text_from_document,
        client: genai.Client | None = None,
    ) -> None:
        self.api_key = api_key or ""
        self.model_name = model_name
        self.document_parser = document_parser
        # Backwards compatibility for callers expecting pdf_parser attribute.
        self.pdf_parser = document_parser
        self._client = client

    def _client_or_create(self) -> genai.Client:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is missing; set it before generating slides.")
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def build_prompt(self, text: str) -> str:
        summary = text[:5000]
        return (
            f"{self._prompt_preamble()}"
            "BOOK TEXT STARTS:\n"
            "-----------------\n"
            f"{summary}\n"
            "-----------------\n"
            "BOOK TEXT ENDS.\n"
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
    )
    def _call_model(self, prompt: str) -> BookConcepts:
        client = self._client_or_create()
        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BookConcepts,
            ),
        )
        return response.parsed

    def generate_from_file(self, document_path: Path | str) -> BookConcepts:
        raw_text = self.document_parser(document_path)
        return self.generate_from_text(raw_text)

    def generate_from_pdf(self, pdf_path: Path | str) -> BookConcepts:
        return self.generate_from_file(pdf_path)

    def generate_from_text(self, text: str) -> BookConcepts:
        prompt = self.build_prompt(text)
        return self._call_model(prompt)

    def build_rag_prompt(
        self,
        *,
        chunks: Sequence[str],
        reference_concept: str | None = None,
        user_context: str | None = None,
    ) -> str:
        trimmed_chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
        limited_chunks = trimmed_chunks[:8]
        passages = "\n\n---\n".join(limited_chunks)
        contextual_lines: list[str] = []
        if reference_concept:
            contextual_lines.append(
                f"Mirror the emotional arc, pacing, and hook strength of the concept titled '{reference_concept}'."
            )
        if user_context:
            contextual_lines.append(f"User direction: {user_context.strip()}")
        if not contextual_lines:
            contextual_lines.append("Use the retrieved passages to craft a new viral-ready concept.")
        context_block = "\n".join(contextual_lines)
        passages_label = "BOOK PASSAGES FOR FACTUAL GROUNDING:"
        return (
            f"{self._prompt_preamble()}"
            "ADDITIONAL DIRECTION:\n"
            f"{context_block}\n\n"
            f"{passages_label}\n"
            "BOOK TEXT STARTS:\n"
            "-----------------\n"
            f"{passages}\n"
            "-----------------\n"
            "BOOK TEXT ENDS.\n"
        )

    @staticmethod
    def _prompt_preamble() -> str:
        return (
            "You are a literary scout extracting VIRAL-READY STORY CONCEPTS from a nonfiction book.\n\n"

        "Your job is to output structured JSON with an array called `concepts`, where each concept\n"
        "has a `name`, `description`, and a list of `slides`.\n\n"

        "RULES FOR CONCEPTS:\n"
        "1. Each concept should be centered on a compelling, emotionally loaded idea or story beat.\n"
        "2. It should be something that could be turned into a short, viral slideshow reel.\n"
        "3. Focus on ideas that are surprising, morally or emotionally intense, or reveal a big hidden truth.\n"
        "4. Keep concepts distinct from each other. No near-duplicates.\n\n"

        "RULES FOR SLIDES:\n"
        "1. Each concept becomes a slideshow of multiple slides (like a TikTok/IG carousel).\n"
        "2. Each slide is ~10–15 words, easy to read, and fully understandable on its own.\n"
        "3. The FIRST slide must be a 1-second hook: shocking, emotional, or curiosity-inducing.\n"
        "4. Subsequent slides should unfold the story or idea step by step.\n"
        "5. Every slide should feel like a 'hit' — an insight, twist, or violation of expectations.\n"
        "6. Avoid academic tone. Write like you're explaining to a smart friend.\n\n"

        "WHAT MAKES VIRALITY:\n"
        "- The average person with no background would be *surprised* by this.\n"
        "- There are real-world stakes or implications for the reader.\n"
        "- It evokes strong emotion (anger, awe, sadness, wonder, injustice, etc.).\n"
        "- The hook makes the reader think: 'Wait, WHAT?' and want to keep going.\n\n"

        "Here are some example types of concepts that work well for virality\n"

        "1. Clean Curiosity Gap\n"
        "Hook: “This ONE sentence quietly controls your spending… and you’ve heard it since kindergarten.”\n"
        "This ONE sentence quietly controls your spending\n"
        "You’ve heard it since kindergarten\n"
        "It sounds innocent\n"
        "But it trains you to feel “less than” forever\n"
        "“You can have it if you’re good”\n"
        "Not “You deserve safety either way”\n"
        "So you chase purchases that prove you’re “good enough”\n"
        "And wonder why money never feels like “enough”\n"

        "2. Contrarian “Everyone Is Wrong About X”\n"
        "Hook: “No, waking up at 5 a.m. is probably making your life worse.”\n"
        "No, waking up at 5 a.m. might be ruining your life\n"
        "Not because early is bad\n"
        "But because you copied a stranger’s schedule\n"
        "For a brain you’ve never met\n"
        "Sleep scientists track your natural energy peaks\n"
        "Yours might be 10 a.m., not 5\n"
        "When you fight your biology, your willpower becomes caffeine\n"
        "Design your day around your brain\n"
        "Not around a productivity influencer’s alarm clock\n"

        "3. Shocking Statistic / Number\n"
        "Hook: “One habit predicts divorce with 91% accuracy — and it’s not cheating.”\n"
        "One habit predicts divorce with 91% accuracy\n"
        "It’s not cheating\n"
        "It’s not screaming matches\n"
        "It’s something quieter\n"
        "In one study, couples were wired to monitors during arguments\n"
        "The biggest red flag wasn’t volume\n"
        "It was contempt\n"
        "Eye rolls\n"
        "Sarcasm\n"
        "Micro-dismissals\n"
        "The moment you start treating your partner as “beneath” you\n"
        "The clock quietly starts counting down\n"

        "4. Confession / “I Was the Problem”\n"
        "Hook: “I’m a happiness researcher… and I accidentally designed the perfect recipe for misery.”\n"
        "I’m a happiness researcher\n"
        "And I accidentally built the perfect life for misery\n"
        "I had the right job title\n"
        "The right city\n"
        "The right LinkedIn posts\n"
        "I optimized every hour for “achievement”\n"
        "And left zero minutes for “nothing”\n"
        "Then I read my own data\n"
        "Joy spikes in the useless moments\n"
        "Walks with no steps goal\n"
        "Conversations with no agenda\n"
        "I wasn’t failing at happiness\n"
        "I was over-optimizing it\n"

        "5. “You vs. Problem” Call-Out\n"
        "Hook: “If you can’t finish books anymore, the problem isn’t your attention span.”\n"
        "If you can’t finish books anymore\n"
        "The problem isn’t your attention span\n"
        "Your brain still focuses for hours… on the right bait\n"
        "Apps train you with tiny jackpot hits\n"
        "Every swipe, a lottery ticket\n"
        "Books don’t scream for you\n"
        "So they feel “boring”\n"
        "But it’s not boredom\n"
        "It’s withdrawal\n"
        "Rebuild your tolerance for quiet pages\n"
        "And suddenly your “broken” attention was just… hijacked\n"

        "6. Tiny Detail → Massive Consequence (Domino Hook)\n"
        "Hook: “The word you use instead of ‘sorry’ changes how much people respect you.”\n"
        "The word you use instead of “sorry”\n"
        "Quietly changes how much people respect you\n"
        "“I’m sorry I’m late”\n"
        "Versus\n"
        "“Thank you for waiting for me”\n"
        "One centers your mistake\n"
        "The other centers their kindness\n"
        "Over hundreds of interactions\n"
        "You stop shrinking\n"
        "They feel seen\n"
        "Same event\n"
        "Different sentence\n"
        "Completely different story your life tells about you\n"


        "OUTPUT FORMAT (very important):\n"
        "- You MUST return JSON compatible with this schema:\n"
        "  {\n"
        "    \"concepts\": [\n"
        "      {\n"
        "        \"name\": \"...\",\n"
        "        \"description\": \"...\",\n"
        "        \"slides\": [\"slide 1 text\", \"slide 2 text\", ...]\n"
        "      },\n"
        "      ...\n"
        "    ]\n"
        "  }\n\n"
        )

    def generate_from_chunks(
        self,
        *,
        chunks: Sequence[str],
        reference_concept: str | None = None,
        user_context: str | None = None,
    ) -> BookConcepts:
        prompt = self.build_rag_prompt(
            chunks=chunks,
            reference_concept=reference_concept,
            user_context=user_context,
        )
        return self._call_model(prompt)
