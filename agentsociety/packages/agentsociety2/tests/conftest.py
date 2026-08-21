import os


def _ensure_llm_env_for_tests() -> None:
    if not (os.environ.get("AGENTSOCIETY_LLM_API_KEY") or "").strip():
        os.environ["AGENTSOCIETY_LLM_API_KEY"] = "test-key"
    if not (os.environ.get("AGENTSOCIETY_LLM_API_BASE") or "").strip():
        os.environ["AGENTSOCIETY_LLM_API_BASE"] = "https://api.openai.com/v1"


_ensure_llm_env_for_tests()
