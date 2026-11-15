"""Translation providers behind one interface (see base.Provider) + the fallback orchestrator.

To add a provider: subclass base.Provider in a module, then register an instance in REGISTRY below.
Nothing in the pipeline needs to change.
"""
from .cli import CodexCLI, GeminiCLI
from .llm import AnthropicAPI, ClaudeCLI, Gemini, GoogleFree, Ollama, OpenAICompat
from .local_mt import LocalNLLB

REGISTRY = {p.id: p for p in (ClaudeCLI(), CodexCLI(), GeminiCLI(), AnthropicAPI(), OpenAICompat(), Gemini(),
                              Ollama(), GoogleFree(), LocalNLLB())}
