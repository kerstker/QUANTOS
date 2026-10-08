"""Gold-specific bounds for the existing factory session/campaign store."""
import re


def remaining_candidates(value):
    if (type(value) is not list or not value or len(value) > 6
            or any(type(n) is not int or not 0 <= n < 6 for n in value)
            or value != sorted(set(value))):
        raise ValueError('invalid gold remaining candidates')
    return list(value)


def _common(config):
    if 'execution_feedback_sha256' in config and (type(config['execution_feedback_sha256']) is not str or
            re.fullmatch('[a-f0-9]{64}',config['execution_feedback_sha256']) is None):
        raise ValueError('invalid gold execution feedback digest')
    if (config.get('market') != 'XAUUSDT' or config.get('mode') != 'ai'
            or type(config.get('budget')) is not int or config['budget'] != 1
            or not isinstance(config.get('objective'), str)
            or not 1 <= len(config['objective'].strip()) <= 2000
            or type(config.get('input_id')) is not int or not 1 <= config['input_id'] <= 1000
            or not isinstance(config.get('input_sha256'), str)
            or re.fullmatch('[0-9a-f]{64}', config['input_sha256']) is None):
        raise ValueError('invalid gold campaign input or scope')


def validate_campaign(config):
    if set(config)-{'execution_feedback_sha256','execution_feedback_mode'} != {'campaign_id', 'market', 'mode', 'budget', 'objective',
                       'max_cycles', 'interval_hours', 'input_id', 'input_sha256'}:
        raise ValueError('invalid gold campaign fields')
    if 'execution_feedback_mode' in config and (config['execution_feedback_mode']!='after_first_round' or
            'execution_feedback_sha256' in config):
        raise ValueError('invalid gold automatic feedback policy')
    _common(config)
    for name, high in (('campaign_id', 1000), ('max_cycles', 6), ('interval_hours', 168)):
        if type(config[name]) is not int or not 1 <= config[name] <= high:
            raise ValueError('invalid gold campaign bound')
    return dict(config)


def validate_session(config):
    if set(config)-{'execution_feedback_sha256','research_memory'} != {'attempt', 'market', 'mode', 'budget', 'objective',
                       'input_id', 'input_sha256', 'remaining_candidates'}:
        raise ValueError('invalid gold session fields')
    _common(config)
    if type(config['attempt']) is not int or not 1 <= config['attempt'] <= 1000:
        raise ValueError('invalid gold attempt')
    if 'research_memory' in config:
        from services.research.gold_research_memory import validate_memory
        validate_memory(config['research_memory'])
    remaining_candidates(config['remaining_candidates'])
    return dict(config)
