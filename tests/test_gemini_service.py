from litreel.services.gemini_runner import BookConcepts, GeminiSlideshowGenerator, SlideConcept


class FakeGeminiClient:
    def __init__(self, parsed):
        self.parsed = parsed
        self.captured_prompt = None
        self.models = self

    def generate_content(self, model, contents, config):  # pragma: no cover - interface shim
        self.captured_prompt = contents
        return type("Resp", (), {"parsed": self.parsed})()


def test_build_prompt_mentions_rules(sample_pdf):
    service = GeminiSlideshowGenerator(api_key="test", model_name="fake")
    prompt = service.build_prompt("Full text for prompt testing")
    assert "VIRAL-READY" in prompt
    assert "BOOK TEXT STARTS" in prompt


def test_rag_prompt_reuses_original_instructions():
    service = GeminiSlideshowGenerator(api_key="test", model_name="fake")
    prompt = service.build_rag_prompt(
        chunks=["Passage 1", "Passage 2"],
        reference_concept="Original Hook",
        user_context="Keep it suspenseful",
    )
    assert "VIRAL-READY" in prompt  # ensures base instructions reused
    assert "ADDITIONAL DIRECTION" in prompt
    assert "Original Hook" in prompt
    assert "Keep it suspenseful" in prompt
    assert "BOOK TEXT STARTS" in prompt


def test_generate_from_pdf_uses_client(sample_pdf):
    parsed = BookConcepts(
        concepts=[
            SlideConcept(name="Concept", description="Desc", slides=["A", "B"])
        ]
    )
    client = FakeGeminiClient(parsed)
    service = GeminiSlideshowGenerator(
        api_key="key",
        model_name="fake",
        client=client,
        document_parser=lambda _: "Example text",
    )
    result = service.generate_from_pdf(sample_pdf)
    assert result == parsed
    assert "Example text" in client.captured_prompt
