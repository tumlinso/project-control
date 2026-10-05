"""Native structured-output hints reach the central observer backend unchanged."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from project_control.observer_analysis import SkillsObserverAnalysisProvider


def test_optional_response_format_is_forwarded_as_an_independent_object():
    backend = SimpleNamespace(run_observer_turn=Mock(return_value={'status': 'available'}))
    provider = SkillsObserverAnalysisProvider()
    provider._checked_client = Mock(return_value=backend)
    response_format = {
        'type': 'json_object',
        'schema': {'type': 'object', 'properties': {'answer': {'type': 'string'}}},
    }
    expected = deepcopy(response_format)
    result = provider.investigate_turn({
        'messages': [{'role': 'user', 'content': 'Return JSON.'}],
        'response_format': response_format,
    })

    assert result == {'status': 'available'}
    forwarded = backend.run_observer_turn.call_args.args[0]
    assert forwarded['response_format'] == expected
    assert forwarded['response_format'] is not response_format


def test_absent_response_format_keeps_legacy_backend_request_shape():
    backend = SimpleNamespace(run_observer_turn=Mock(return_value={'status': 'available'}))
    provider = SkillsObserverAnalysisProvider()
    provider._checked_client = Mock(return_value=backend)

    assert provider.investigate_turn({
        'messages': [{'role': 'user', 'content': 'Return JSON.'}],
    }) == {'status': 'available'}
    assert 'response_format' not in backend.run_observer_turn.call_args.args[0]
