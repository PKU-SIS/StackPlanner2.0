"""Qwen transport compatibility only; no SP2 workflow or evidence changes."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from deerflow.models.vllm_provider import VllmChatModel


def normalize_system_messages(messages):
    systems = [m for m in messages if m.get('role') == 'system']
    if len(systems) <= 1 and (not systems or messages[0].get('role') == 'system'):
        return messages
    if any(set(m) - {'role', 'content'} for m in systems):
        raise ValueError('unsupported_system_message_fields')
    contents = [m.get('content', '') for m in systems]
    if all(isinstance(c, str) for c in contents):
        content = '\n\n'.join(contents)
    else:
        content = []
        for c in contents:
            if isinstance(c, str): content.append({'type': 'text', 'text': c})
            elif isinstance(c, list): content.extend(c)
            else: raise ValueError('unsupported_system_content')
    return [{'role': 'system', 'content': content}] + [m for m in messages if m.get('role') != 'system']


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def strict_json(text):
    def invalid_constant(value):
        raise ValueError('non_json_constant')
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=invalid_constant)


def _resolve(schema, root):
    seen = set()
    while isinstance(schema, dict) and '$ref' in schema:
        ref = schema['$ref']
        if ref in seen or not ref.startswith('#/'): return {}
        seen.add(ref)
        value = root
        try:
            for part in ref[2:].split('/'):
                value = value[part.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError): return {}
        schema = value
    return schema if isinstance(schema, dict) else {}


def _types(schema, root):
    schema = _resolve(schema, root)
    typ = schema.get('type')
    result = {typ} if isinstance(typ, str) else set(typ or [])
    for branch in schema.get('anyOf', []) + schema.get('oneOf', []):
        result |= _types(branch, root)
    return result


def normalize_argument(value, schema, root=None):
    """Decode only schema-declared containers. Leave bad/scalar inputs to validation."""
    root = schema if root is None else root
    schema = _resolve(schema, root)
    allowed = _types(schema, root)
    if isinstance(value, str) and 'string' not in allowed and allowed & {'array', 'object'}:
        try: decoded = strict_json(value)
        except (ValueError, TypeError): pass
        else:
            if ('array' in allowed and isinstance(decoded, list)) or ('object' in allowed and isinstance(decoded, dict)):
                value = decoded
    for branch in schema.get('anyOf', []) + schema.get('oneOf', []):
        types = _types(branch, root)
        if (isinstance(value, list) and 'array' in types) or (isinstance(value, dict) and 'object' in types):
            return normalize_argument(value, branch, root)
    if isinstance(value, dict) and 'object' in allowed:
        properties = schema.get('properties') or {}
        return {key: normalize_argument(item, properties[key], root) if key in properties else item for key, item in value.items()}
    if isinstance(value, list) and 'array' in allowed and isinstance(schema.get('items'), dict):
        return [normalize_argument(item, schema['items'], root) for item in value]
    return value


def _schemas(tools):
    result = {}
    for tool in tools or []:
        if isinstance(tool, dict):
            function = tool.get('function', {})
            if function.get('name') and isinstance(function.get('parameters'), dict):
                result[function['name']] = function['parameters']
    return result


def _trace(record):
    """Metadata only: never log reasoning, answer text, or argument values."""
    folder = os.environ.get('QWEN_ADAPTER_TRACE_DIR')
    if not folder: return
    directory = Path(folder)
    directory.mkdir(parents=True, exist_ok=True)
    record = dict(time=time.time(), **record)
    with (directory / 'transport.jsonl').open('a') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')


def _response_shape(content, calls, *, has_reasoning_field=False):
    """Observe public answer/protocol shape without reading hidden reasoning."""
    return {
        'visible_content_type': type(content).__name__,
        'visible_text_chars': len(content) if isinstance(content, str) else 0 if content is None else None,
        'tool_call_count': len(calls) if isinstance(calls, list) else 0 if calls is None else None,
        'reasoning_field_present': has_reasoning_field,
    }


def normalize_result(result, tools):
    schemas = _schemas(tools)
    for generation in result.generations:
        message = generation.message
        calls = getattr(message, 'tool_calls', None) or []
        normalized = []
        for call in calls:
            schema = schemas.get(call.get('name'))
            args = normalize_argument(call['args'], schema) if schema else call['args']
            normalized.append({**call, 'args': args})
            if args != call['args']:
                _trace(dict(kind='schema_container_decode', tool=call['name'], call_id=call.get('id'), before_types={k:type(v).__name__ for k,v in call['args'].items()}, after_types={k:type(v).__name__ for k,v in args.items()}))
        message.tool_calls = normalized
    return result


class QwenSPChatModel(VllmChatModel):
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload['messages'] = normalize_system_messages(payload.get('messages', []))
        return payload

    def _create_chat_result(self, response, generation_info=None):
        raw = response if isinstance(response, dict) else response.model_dump()
        _trace(dict(kind='provider_response_shape', response_id=raw.get('id'), choices=[
            dict(finish_reason=choice.get('finish_reason'), **_response_shape(
                (choice.get('message') or {}).get('content'),
                (choice.get('message') or {}).get('tool_calls'),
                has_reasoning_field=any(key in (choice.get('message') or {}) for key in ('reasoning', 'reasoning_content')),
            )) for choice in raw.get('choices', [])
        ]))
        for choice in raw.get('choices', []):
            message = choice.get('message') or {}
            for call in message.get('tool_calls') or []:
                function = call.get('function') or {}
                arguments = function.get('arguments')
                try: args = strict_json(arguments) if isinstance(arguments, str) else arguments
                except (ValueError, TypeError): args = None
                _trace(dict(kind='provider_tool_shape', response_id=raw.get('id'), tool=function.get('name'), call_id=call.get('id'), argument_types={k:type(v).__name__ for k,v in args.items()} if isinstance(args, dict) else None))
        result = super()._create_chat_result(response, generation_info)
        _trace(dict(kind='parsed_response_shape', response_id=raw.get('id'), choices=[
            dict(invalid_tool_call_count=len(getattr(generation.message, 'invalid_tool_calls', None) or []), **_response_shape(
                generation.message.content,
                getattr(generation.message, 'tool_calls', None),
                has_reasoning_field=any(key in generation.message.additional_kwargs for key in ('reasoning', 'reasoning_content')),
            )) for generation in result.generations
        ]))
        _trace(dict(kind='response_usage', response_id=raw.get('id'), finish_reasons=[c.get('finish_reason') for c in raw.get('choices',[])], usage=raw.get('usage')))
        return result

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return normalize_result(result, kwargs.get('tools'))

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return normalize_result(result, kwargs.get('tools'))
