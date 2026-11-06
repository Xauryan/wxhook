"""Verified provider metadata. Token capacities are separate from app text budgets."""
MODEL_CAPABILITIES = {
    'deepseek-flash': {
        'context_window_tokens': 1048576,
        'max_output_tokens': 393216,
        'reasoning_efforts': ['none', 'low', 'high', 'max'],
        'default_reasoning_effort': 'high',
        'source': 'https://api-docs.deepseek.com/api/list-models/',
    },
    'deepseek-v4-pro': {
        'context_window_tokens': 1048576,
        'max_output_tokens': 393216,
        'reasoning_efforts': ['none', 'low', 'high', 'max'],
        'default_reasoning_effort': 'high',
        'source': 'https://api-docs.deepseek.com/quick_start/pricing/',
    },
    'gpt-6-astra': {
        'context_window_tokens': 1050000,
        'max_output_tokens': 128000,
        'reasoning_efforts': ['low', 'medium', 'high', 'xhigh', 'max'],
        'default_reasoning_effort': 'medium',
        'source': 'https://developers.openai.com/api/docs/models/gpt-6-astra',
    },
}

MODEL_CAPABILITIES['gpt-6.1-sol'] = {
    **MODEL_CAPABILITIES['gpt-6-astra'],
    'source': 'https://developers.openai.com/api/docs/models/gpt-6.1-sol',
}
MODEL_CAPABILITIES['gpt-6-luna'] = {
    **MODEL_CAPABILITIES['gpt-6-astra'],
    'reasoning_efforts': ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    'source': 'https://developers.openai.com/api/docs/models/gpt-6-luna',
}


def capabilities(model):
    name = model.strip().lower()
    if name.startswith('gpt-6-astra-'):
        name = 'gpt-6-astra'
    return MODEL_CAPABILITIES.get(name)
