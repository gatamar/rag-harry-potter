# ✨ Harry Potter RAG (Educational Demo)

Welcome to a local Retrieval-Augmented Generation walkthrough built around *Harry Potter and the Sorcerer's Stone*. This project is intentionally educational: it shows how to parse an EPUB, build a vector index, wire tracing/logging, and surface answers with LangChain—*all on your laptop*.

## 🛠️ Tech Stack

- **Python 3.11** for all scripts and tooling.
- **ebooklib + BeautifulSoup + lxml** to parse the EPUB into JSONL chunks with provenance metadata.
- **SentenceTransformers + FAISS** for dense embeddings and similarity search.
- **LangChain 0.2** for retriever → prompt → LLM orchestration.
- **OpenTelemetry + Jaeger** for local tracing across parsing, embedding, retrieval, and answer synthesis.
- **SQLite** as a retrieval ledger capturing every query and its candidate set.

## ⚙️ How It Works (example)

```bash
(venv) olia@macbookpro-1 rag-harry-potter % python tools/answer.py "who was Harry teachers? list 5"
huggingface/tokenizers: The current process just got forked, after parallelism has already been used. Disabling parallelism to avoid deadlocks...
To disable this warning, you can either:
	- Avoid using `tokenizers` before the fork if possible
	- Explicitly set the environment variable TOKENIZERS_PARALLELISM=(true | false)
huggingface/tokenizers: The current process just got forked, after parallelism has already been used. Disabling parallelism to avoid deadlocks...
To disable this warning, you can either:
	- Avoid using `tokenizers` before the fork if possible
	- Explicitly set the environment variable TOKENIZERS_PARALLELISM=(true | false)
Harry's teachers included:

1. Professor McGonagall - Transfiguration (chunk: index_split_008.html-0002)
2. Professor Quirrell - Defense Against the Dark Arts (chunk: index_split_005.html-0008)
3. Professor Flitwick - Charms (chunk: index_split_008.html-0002)
4. Professor Binns - History of Magic (chunk: index_split_008.html-0002)
5. Professor Snape - Potions (chunk: index_split_007.html-0014)

Context chunks:
- index_split_008.html-0002 (score=0.5514)
- index_split_005.html-0008 (score=0.4642)
- index_split_007.html-0014 (score=0.4602)
- index_split_014.html-0001 (score=0.4580)
- index_split_005.html-0020 (score=0.4526)
```

The CLI loads the FAISS index, retrieves the top similarity hits, injects them into the answer prompt, and formats a response with chunk references. Traces are emitted to Jaeger (so you can inspect spans like `rag.answer.embed` and `rag.answer.llm`).

## 🚀 Getting Started

1. **Create a Python 3.11 environment** and install dependencies (`pip install -r requirements.txt`).
2. **Initialize artifacts:** run `python flows/rag_pipeline.py` to parse and index the EPUB.
3. **Set credentials:** place your OpenAI key (or local provider settings) in a `.env` file (`OPENAI_API_KEY=...`).
4. **Start Jaeger** locally (`./tools/jaeger/jaeger-all-in-one --config=file:telemetry/jaeger-config.yaml`) to capture OpenTelemetry spans.
5. **Ask a question:** `python tools/answer.py "your question here"`.

This repository is a learning sandbox—clone it, follow the design doc, and adapt it to your own corpus. Happy experimenting! 🧙‍♂️
