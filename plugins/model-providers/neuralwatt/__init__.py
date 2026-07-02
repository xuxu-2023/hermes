"""Neuralwatt provider profile.

Neuralwatt (https://api.neuralwatt.com/v1) is a fully OpenAI-compatible
endpoint hosting models from several families (GLM 5.x, Kimi K2.x, Qwen 3.x,
MiniMax M2.5, Devstral, gpt-oss).  Model IDs are slash-form (``zai-org/...``,
``moonshotai/...``) the way GMI Cloud's are, so no provider-prefix stripping is
applied — the slash is part of the model ID, not a provider tag.
"""

from providers import register_provider
from providers.base import ProviderProfile

neuralwatt = ProviderProfile(
    name="neuralwatt",
    aliases=("neural-watt", "neuralwatt-ai"),
    display_name="Neuralwatt",
    description="Neuralwatt — multi-model OpenAI-compatible direct API",
    signup_url="https://portal.neuralwatt.com/",
    env_vars=("NEURALWATT_API_KEY", "NEURALWATT_BASE_URL"),
    base_url="https://api.neuralwatt.com/v1",
    auth_type="api_key",
    default_aux_model="glm-5-fast",
    fallback_models=(
        "zai-org/GLM-5.1-FP8",
        "glm-5.2",
        "glm-5-fast",
        "moonshotai/Kimi-K2.5",
        "moonshotai/Kimi-K2.7-Code",
        "MiniMaxAI/MiniMax-M2.5",
        "Qwen/Qwen3.5-397B-A17B-FP8",
        "mistralai/Devstral-Small-2-24B-Instruct-2512",
        "openai/gpt-oss-20b",
    ),
)

register_provider(neuralwatt)
