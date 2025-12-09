import os, re, json, pathlib, fitz
from google.colab import drive

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


# ---- Drive (don’t remount if already mounted) ----
if not os.path.ismount("/content/drive"):
    drive.mount('/content/drive')

# ---- CONFIG ----
# SECURITY: don't hard-code your API key.
# For example in Colab:
#   from google.colab import userdata
#   os.environ["GEMINI_API_KEY"] = userdata.get("GEMINI_API_KEY")
API_KEY = "AIzaSyDMrR496uY3Ja94jdDd4NwLzT1CM88DgxY" 
os.environ["GEMINI_API_KEY"] = API_KEY
if not API_KEY:
    raise RuntimeError("Please set GEMINI_API_KEY in your environment.")

# Current model ids (late 2025): gemini-2.5-flash / gemini-2.5-pro
MODEL_NAME = "gemini-2.5-pro"

MYDRIVE_ROOT = pathlib.Path("/content/drive/MyDrive")
FILE_STEM = "skloot-immortal-life-of-henrietta-lacks"

# Where to write the final concepts+slides JSON
OUTPUT_PATH = pathlib.Path("/content/book_concepts_slides.json")


# ==== NEW STRUCTURED OUTPUT SCHEMA (concepts -> slides) ====

class Concept(BaseModel):
    """
    One viral-ready concept, containing a list of slides.
    """
    name: str = Field(
        description="A short, punchy title or hook for this concept (3–8 words)."
    )
    description: str = Field(
        description="1–2 sentences summarizing what this concept is about."
    )
    slides: list[str] = Field(
        description=(
            "An ordered list of slides. Each slide is a single sentence or phrase "
            "of ~10–15 words that is self-contained and easy to read."
        )
    )


class BookConcepts(BaseModel):
    """
    Top-level structured output: a list of concepts for the whole book.
    """
    concepts: list[Concept] = Field(
        description="A list of the most interesting, viral-ready concepts from the book."
    )


# ---- Helpers ----

def find_pdf(base_dir: pathlib.Path, stem: str) -> pathlib.Path:
    cands = [p for p in base_dir.rglob("*.pdf")
             if p.stem.lower() == stem.lower() or p.name.lower() == stem.lower() + ".pdf"]
    if not cands:
        cands = [p for p in base_dir.rglob("*.pdf") if stem.lower() in p.stem.lower()]
    if not cands:
        raise FileNotFoundError(f"Could not find a PDF matching '{stem}' in {base_dir}")
    # choose shortest path (usually the “main” one)
    return sorted(cands, key=lambda x: len(str(x)))[0]


def extract_full_book_text(doc: fitz.Document) -> str:
    """
    Extracts text from the entire PDF as one string.
    """
    parts = []
    for i in range(doc.page_count):
        page_text = doc.load_page(i).get_text("text")
        if page_text:
            parts.append(page_text.strip())

    text = "\n".join(parts)
    # Clean up spacing a bit
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def build_prompt(full_text: str) -> str:
    """
    Prompt that asks for 'concepts' each of which contains a list of 'slides'.
    You said you already wrote a new prompt for slides; you can tweak this
    template to exactly match your version.
    """
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

        "Now read the ENTIRE book text below and extract multiple viral-ready concepts.\n"
        "Focus on the most powerful, story-worthy material. Do NOT just summarize each chapter;\n"
        "instead, pick the most viral concepts in the whole book.\n\n"

        "BOOK TEXT STARTS:\n"
        "-----------------\n"
        f"{full_text}\n"
        "-----------------\n"
        "BOOK TEXT ENDS.\n"
    )


# ---- Gemini client (google-genai with structured output) ----

client = genai.Client(api_key=API_KEY)

# Optional: sanity-check model name
try:
    models = list(client.models.list())
    names = {m.name for m in models}
    if MODEL_NAME not in names and f"models/{MODEL_NAME}" not in names:
        print("Warning: chosen model not in your accessible list; a few available Gemini models:")
        print(sorted([n for n in names if "gemini" in n])[:10])
except Exception as e:
    print(f"(Note) Couldn’t list models: {e}")


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception_type(Exception),
)
def call_gemini(prompt: str) -> BookConcepts:
    """
    Call Gemini with structured output.
    Returns a BookConcepts Pydantic model:
      { "concepts": [ {name, description, slides: [...]}, ... ] }
    """
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BookConcepts,  # <-- NEW structured output
        ),
    )
    # response.parsed is already a BookConcepts instance
    return response.parsed


# ---- Main: process the WHOLE book at once ----

pdf_path = find_pdf(MYDRIVE_ROOT, FILE_STEM)
print(f"Using PDF: {pdf_path}")

doc = fitz.open(pdf_path)
total_pages = doc.page_count
print(f"Total pages: {total_pages}")

full_text = extract_full_book_text(doc)
doc.close()

print("Extracted full book text. Calling Gemini once for concepts + slides...")

prompt = build_prompt(full_text)

try:
    book_concepts = call_gemini(prompt)  # BookConcepts
except Exception as e:
    raise RuntimeError(f"Error analyzing book with Gemini: {e}")

# Serialize to JSON for downstream use
with open(OUTPUT_PATH, "w", encoding="utf-8") as fout:
    json.dump(book_concepts.model_dump(), fout, ensure_ascii=False, indent=2)

print(f"\nDone. Wrote concepts+slides JSON to: {OUTPUT_PATH}")
