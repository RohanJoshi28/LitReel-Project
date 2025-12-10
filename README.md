# Litreel
Premise: Upload nonfiction books to get engaging micro-lessons that you can download and post on social media platforms. 

## What It Does
Litreel transforms long-form nonfiction books into educational short reels for marketing on TikTok, Instagram Reels, and YouTube Shorts. Writers upload a PDF, and the platform automatically generates "**micro-lessons**"—thematic slideshows about key ideas or takeaways that can be exported as short videos. Each micro-lesson/slideshow has a hook, description, and ordered sequence of short-form text designed for viral engagement. The core pipeline starts with document ingestion, where uploaded books are parsed and passed to Gemini 2.5 Pro, which extracts multiple viral-ready "micro-lessons" representing distinct interesting or emotional events, statistics, or ideas from the source material. Book text is also chunked and embedded into a vector store (Supabase Postgres on prod or local SQLite in dev) to power **Retrieval-Augmented Generation (RAG)**: the **Micro-Lesson Lab** feature lets users generate fresh micro-lessons by remixing existing ones or providing context on a new direction/idea they want to use. Additionally, users can select **emotionally charged mode**, which samples ~30 random chunks from the book, ranks them by emotional arousal using a fine-tuned MiniLM-L6 transformer (see [`ML_training/`](ML_training/) for training notebooks), and passes only the highest-scoring passages to Gemini—ensuring new micro-lessons are grounded in the book's most emotionally charged moments. Once slides are finalized, Litreel generates voiceover audio via the Lemonfox TTS (text to speech) API, adding narration to each slide's text for a polished, audio-synced viewing experience. The user's can then add image backgrounds to each slide using Pexels stock footage and render a reel from the slides with the final MP4 uploaded to Supabase Storage and made available via signed download URLs. A Flask + HTML/javascript frontend lets authors edit slide text, reorder slides, swap background images, preview voices that they like, and export finished reels—all in the browser.

## Video Links
- Demo Video: https://drive.google.com/file/d/15R3BxnsWB-2TjqORmbFhAT2uO1OQMik4/view?usp=sharing
- Technical Walkthrough: https://drive.google.com/file/d/1oSkLbsW62LZvZBMMGJTS1hXBvmmsTM_N/view?usp=sharing

## Quick Start
1. **Open the creation studio:** My website is hosted at https://litreel-9f4b585284df.herokuapp.com/ 
2. **Sign up with your own account or use the QA (testing) account:** `testuser@litreel.app` / `TestDrive123!` allows for easy access to a testing account you can use without creating your own account.
3. **Do you need to run locally?** Follow the detailed steps in `SETUP.md` to provision dependencies and environment variables.

**Try the Emotional Arousal Model:** The fine-tuned MiniLM-L6 transformer that powers emotional arousal ranking during RAG is deployed as a standalone Hugging Face Space at [https://huggingface.co/spaces/RohanJoshi28/narrative-arousal-regressor](https://huggingface.co/spaces/RohanJoshi28/narrative-arousal-regressor) that **you can test!** (might take a second to boot up cpu) In the actual project, this model scores ~30 random book chunks and selects the most emotionally charged passages to send to Gemini for micro-lesson/viral reel generation. You can test it independently—paste any narrative text and see its predicted arousal score (Gaussian-distributed, centered around 0, where higher values indicate more emotionally intense passages).

## Technical Overview

### Brief overview of the Architecture
- **Flask backend + HTML/javascript frontend:** A single Flask process serves `/` (landing) and `/studio`, exposes REST APIs under `/api`, and relies on Flask-Login for sessions.
- **Document ingestion → Gemini micro-lesson extraction:** Uploads (PDF, DOCX, EPUB) are parsed, chunked, and summarized by Gemini models to produce micro-lesson broken down into individual slides. LemonFox API adds text to speech to narrate each slide when the user clicks the "Render" button. 
- **Retrieval-Augmented Generation:** Supabase (or SQLite when `DATABASE_PROFILE=local`) stores embedded `book`/`book_chunk` rows in a vector store. When the user utilizes the concept lab (RAG feature), Gemini prompts pull context through RAG lookups before generating viral micro-lessons based on user-driven direction or existing micro-lessons that users want to remix. 
- **Background jobs + rendering:**  Because Heroku enforces short request timeouts, long-running Gemini and RAG operations are offloaded to Redis-backed RQ queues. `worker.py` handles slide generation, Concept Lab (RAG) calls, and MP4 rendering, uploading finished reels to Supabase Storage in prod with signed download URLs.

[![image](https://github.com/RohanJoshi28/LitReel-Project/blob/main/system_components.png)](https://github.com/RohanJoshi28/LitReel-Project/blob/main/system_components.png)

### Retrieval & Emotion Ranking
Litreel supports two distinct RAG modes for generating new micro-lessons in the Micro-Lesson Lab:

- **Directed RAG (User Context / Remix):** When users provide custom context or select an existing micro-lesson to remix, the system performs semantic search against the book's vector store (Supabase Postgres in prod, SQLite locally) using RPCs like `match_book_chunks`. The most relevant passages are retrieved and passed to Gemini alongside the user's creative direction, grounding new micro-lessons in contextually appropriate source material.

- ** Emotionally Charged Mode (Emotion-Ranked):** When users select emotionally charged mode, the system samples ~30 random chunks from the book and scores each one using a fine-tuned MiniLM-L6 transformer trained on narrative emotional arousal data (see [`ML_training/`](ML_training/) jupyter notebooks; deployed as a huggingface API). Only the top-scoring passages—those with the highest predicted emotional intensity—are sent to Gemini, ensuring new micro-lessons emerge from the book's most emotionally charged moments rather than arbitrary text.

[![image](https://github.com/RohanJoshi28/LitReel-Project/blob/main/TechnicalArchitecture.jpeg)](https://github.com/RohanJoshi28/LitReel-Project/blob/main/TechnicalArchitecture.jpeg)

### Supabase Schema Explanation
- **`users`:** Stores registered accounts (email + hashed password) for authentication via Flask-Login.
- **`projects`:** The central entity tying everything together. Each project represents one uploaded book and links to its source material via `supabase_book_id` (referencing the `books`/`book_chunks` vector store tables). Projects track the currently micro-lesson the user is working on (`active_concept_id`), chosen voice for text to speech (Adam, Bella, Liam, or Sarah), and generation status.
- **`concepts`:** Each project can have multiple micro-lessons (called "concepts" in the schema). Each concept has a name, description, and ordering index.
- **`slides`:** Individual slides within a micro-lesson, containing the display text, background image URL, visual effect (zoom, pan), and transition style (fade, slide, scale).
- **`slide_styles`:** Typography overrides per slide—text color, outline color, font weight, and underline settings.
- **`render_artifacts`:** Tracks asynchronous video render jobs (job that actually renders the short-form reel from the slideshow)—job ID, status (queued/processing/complete/failed), Supabase Storage path, signed download URL, file size, etc...
- **`app_logs`:** Stores structured warnings and errors from the application, including request IDs, user context, and stack traces. Persisted in the database so production redeploys don't delete these logs. 

### Error Handling & Logging
- **Request tracing:** Every API request gets a unique ID attached, making it easy to trace issues across logs and debug specific user problems.
- **Structured JSON logs:** Logs are written in JSON format with useful context (route, user, response time, etc.) and saved to `instance/logs/litreel.log`.
- **Log verbosity levels:** Use `LOG_VERBOSITY` to control how much gets logged—`none` for silence, `essential` for warnings/errors only, or `verbose` for full debugging output.
- **Database log backup:** When Supabase credentials are configured, warnings and errors are also saved to the `app_logs` table for long-term storage.
- **Error Handling:** When users visit a path that doesn't exist, they see a friendly error page instead of a generic browser error. Many features like the upload book feature, RAG feature, and text to speech feature are wrapped in try/catch blocks that display user-friendly error messages. Additionally, I added a rate limit of one book getting processed at a time. 

## Evaluation
A transformer model was trained to predict emotional arousal from literature datasets like "Cr4-NarrEmote" and "Automatic Emotion Modelling in Written Stories" in order to assist in selecting emotionally charged passages for compelling short-form content. The Cr4-NarrEmote dataset contains roughly 1.1 million tokens (attached in this repository in [ML_training/data/cr4](ML_training/data/cr4)) while the Automatic Emotion Modelling in Written Stories dataset contains roughly 250k tokens (attached in this repository in [ML_training/data/alm](ML_training/data/alm)).

- Qualitatively, the micro-lessons that Gemini come up with are engaging and relevant to the book. Without prompt engineering, the lessons were significantly less engaging. Example micro-lesson from the book "Sapiens": 

[![image](https://github.com/RohanJoshi28/LitReel-Project/blob/main/example_slideshow.png)](https://github.com/RohanJoshi28/LitReel-Project/blob/main/example_slideshow.png)

- Qualitatively, the transformer that predicts emotional arousal to rank book passages also works well. Here are outputs for the following sentences with varying emotional intensity:

| Sentence | Predicted Arousal (Normalized) | Predicted Arousal (Original Scale) |
|----------|-------------------------------|-----------------------------------|
| The train arrived at its scheduled time, and passengers stepped onto the platform. | -2.15 | 0.18 |
| She realized she had misplaced her notebook, which held an important reminder for tomorrow's meeting. | -1.30 | 0.58 |
| The phone rang with an unfamiliar number, and her stomach dropped before she slowly picked it up. | 0.13 | 0.53 |
| She watched her father cry for the first time, realizing he was breaking in a way she couldn't fix. | 0.70 | 0.55 |

As you can see, the normalized predicted arousal keeps getting more positive as the sentence becomes more emotionally charged. The original scale values range from 0 to 1, where higher values indicate greater emotional intensity.

I also quantitatively evaluated the transformer's performance on predicting emotional arousal after training for 5 epochs and optimized hyperparameters. I learned that the Adam optimizer does significantly better than SGD here, and a dropout of 0.05 is a great choice. Using a transformer language model also significantly outperformed TF-IDF baseline - the MSE difference might seem like a small difference but it's actually quite significant given the output scale. I got a spearman of ~83% on the test dataset (measures how well the order (ranking) of the predictions matches the order of the true labels), which is considered pretty good. Here are the results of hyperparameter optimization on validation: 

[![image](https://github.com/RohanJoshi28/LitReel-Project/blob/main/quantitative_transformer_evaluation.png)](https://github.com/RohanJoshi28/LitReel-Project/blob/main/quantitative_transformer_evaluation.png)

I then trained with the model with a dropout of 0.05 & the Adam optimizer (I noticed that L1/L2 regularization was pretty terrible on this problem) for 15 epochs with early stopping [ML_training/EmotionalArousalTransformer.ipynb](ML_training/EmotionalArousalTransformer.ipynb) and got a Spearman correlation coefficient of ~0.85 on the test set, which is considered close to state of the art for this type of task.

## Documentation & Contribution
- `SETUP.md` — detailed install, environment, Supabase, and worker instructions.
- `ATTRIBUTION.md` — references for models, datasets, and third-party docs used in the stack.

### Contribution
I completed this project myself from scratch. 
