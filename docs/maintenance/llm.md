# LLM Credentials Setup

LangChain clients expect provider credentials via environment variables. For OpenAI-compatible models:

1. Create a `.env` file in the project root (do **not** commit it):

```bash
OPENAI_API_KEY=sk-your-key
```

2. Ensure the CLI scripts load it (`python-dotenv` is already wired in `tools/answer.py` and `tools/query.py`).
3. Activate your Python virtualenv and run the tools normally:

```bash
python tools/answer.py "Who brought Harry his letter?"
```

If you switch providers, add the required variables (e.g., `ANTHROPIC_API_KEY`, custom base URLs) to the same `.env` file and update `config.llm` accordingly.
