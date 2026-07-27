import io
import json
import urllib.error
import urllib.request

import pytest

from tracefix.protocol_templates.template import Template
from tracefix.runtime.coordination_patterns import (
    COORDINATION_PATTERNS,
    is_valid_coordination_pattern,
    normalize_coordination_pattern,
    normalize_coordination_patterns,
)
from tracefix.runtime.llm_attribute_extractor import (
    AttributeExtractionError,
    AttributeExtractionResponseError,
    ExtractedCoordinationData,
    ExtractedCoordinationAttributes,
    build_attribute_extraction_prompt,
    extract_coordination_attributes,
    _build_default_client,
)
from tellme_harness.llm_client import OpenAICompatibleLLMClient


def _valid_payload(**overrides):
    payload = {
        "coordination_patterns": ["Request-Grant", "Exclusive Resource Access"],
        "number_of_agents": 3,
        "agent_roles": ["robot", "scheduler", "robot", ""],
        "communication_flow": [],
        "limitations": ["no starvation", "no starvation"],
        "number_of_resources": 1,
        "number_of_channels": 3,
    }
    payload.update(overrides)
    return payload


def test_coordination_pattern_helpers_require_exact_values_and_reject_duplicates():
    assert normalize_coordination_pattern("Request-Grant") == "Request-Grant"
    with pytest.raises(ValueError):
        normalize_coordination_pattern("request-grant")
    with pytest.raises(ValueError, match="duplicate"):
        normalize_coordination_patterns(["Request-Grant", "Request-Grant"])
    assert is_valid_coordination_pattern("Majority Voting") is True
    assert is_valid_coordination_pattern("made up") is False


def test_attributes_accept_valid_empty_and_complete_payloads():
    assert ExtractedCoordinationAttributes.from_payload({
        "coordination_patterns": [],
        "number_of_agents": None,
        "agent_roles": [],
        "communication_flow": [],
        "limitations": [],
        "number_of_resources": None,
        "number_of_channels": None,
    }).as_dict() == Template.empty_coordination_attributes()

    result = ExtractedCoordinationAttributes.from_payload(_valid_payload())

    assert result.as_dict() == {
        "coordination_patterns": ["Request-Grant", "Exclusive Resource Access"],
        "number_of_agents": 3,
        "agent_roles": ["robot", "scheduler"],
        "communication_flow": ["request", "grant", "enter", "exit", "release"],
        "limitations": ["no starvation"],
        "number_of_resources": 1,
        "number_of_channels": 3,
    }

    assert isinstance(result, ExtractedCoordinationData)


@pytest.mark.parametrize(
    "field,value",
    [
        ("number_of_agents", -1),
        ("number_of_agents", True),
        ("number_of_agents", "3"),
        ("number_of_resources", -1),
        ("number_of_channels", False),
    ],
)
def test_attributes_reject_bad_numeric_values(field, value):
    with pytest.raises(AttributeExtractionResponseError):
        ExtractedCoordinationAttributes.from_payload(_valid_payload(**{field: value}))


@pytest.mark.parametrize(
    "extra_key",
    [
        "selected_template",
        "template_id",
        "match_type",
        "confidence",
        "notes",
        "coord_keywords",
        "coordination_keywords",
        "normalized_keywords",
        "explicit_keywords",
        "template_keywords",
    ],
)
def test_attributes_reject_extra_template_classifier_or_metadata_fields(extra_key):
    payload = _valid_payload()
    payload[extra_key] = "not allowed"

    with pytest.raises(AttributeExtractionResponseError):
        ExtractedCoordinationAttributes.from_payload(payload)


def test_attributes_reject_unknown_patterns():
    with pytest.raises(AttributeExtractionResponseError, match="unknown coordination pattern"):
        ExtractedCoordinationAttributes.from_payload(
            _valid_payload(coordination_patterns=["made up pattern"])
        )


@pytest.mark.parametrize("field", Template.COORDINATION_ATTRIBUTE_FIELDS)
def test_attributes_reject_each_missing_canonical_key(field):
    payload = _valid_payload()
    del payload[field]
    with pytest.raises(AttributeExtractionResponseError, match="missing canonical fields"):
        ExtractedCoordinationAttributes.from_payload(payload)


@pytest.mark.parametrize("value", ["Queue Scheduling", "Queue Based Scheduling", "Producer Consumer", "request-grant"])
def test_attributes_reject_aliases_punctuation_and_capitalization(value):
    with pytest.raises(AttributeExtractionResponseError):
        ExtractedCoordinationAttributes.from_payload(_valid_payload(coordination_patterns=[value]))


def test_extractor_rejects_duplicate_json_keys():
    raw = json.dumps(_valid_payload()).replace('"number_of_agents": 3', '"number_of_agents": 3, "number_of_agents": 4')
    with pytest.raises(AttributeExtractionResponseError, match="duplicate JSON key"):
        extract_coordination_attributes("coordinate", client=lambda _request: raw)


def test_every_canonical_pattern_is_accepted():
    for pattern in COORDINATION_PATTERNS:
        result = ExtractedCoordinationAttributes.from_payload(
            _valid_payload(coordination_patterns=[pattern], communication_flow=[])
        )
        assert result.coordination_patterns == [pattern]


def test_prompt_is_extraction_only_and_includes_pattern_vocabulary():
    messages = build_attribute_extraction_prompt("two robots share a corridor")
    rendered = json.dumps(messages)

    assert "Do not select templates" in rendered
    assert "Do not rank templates" in rendered
    assert "Do not return confidence" in rendered
    assert "coordination_keywords" not in rendered
    assert "coord_keywords" not in rendered
    assert "template_keywords" not in rendered
    assert "template_id" not in rendered
    for pattern in COORDINATION_PATTERNS:
        assert pattern in rendered
    assert "canonical TraceFix Template" in rendered
    assert set(Template.empty_coordination_attributes()) == set(
        ExtractedCoordinationAttributes.model_fields
    )


def test_known_patterns_populate_missing_communication_flow_deterministically():
    result = ExtractedCoordinationAttributes.from_payload(
        _valid_payload(
            coordination_patterns=[
                "Exclusive Resource Access",
                "Queue-Based Scheduling",
            ],
            communication_flow=[],
        )
    )

    assert result.communication_flow == [
        "request",
        "grant",
        "enter",
        "exit",
        "release",
        "enqueue",
        "dequeue",
        "complete",
    ]


def test_extractor_uses_dependency_injected_client_and_model():
    seen = {}

    def fake_client(request):
        seen.update(request)
        return _valid_payload(number_of_agents=4)

    result = extract_coordination_attributes(
        "robots share a corridor",
        model="openrouter/test-model",
        client=fake_client,
    )

    assert seen["model"] == "openrouter/test-model"
    assert seen["messages"][0]["role"] == "system"
    assert result.number_of_agents == 4


def test_extractor_accepts_openai_style_response():
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(_valid_payload(number_of_channels=2)),
                }
            }
        ]
    }

    result = extract_coordination_attributes("robots coordinate", client=lambda _req: response)

    assert result.number_of_channels == 2


@pytest.mark.parametrize("response", ["", "not json", "[]", None])
def test_extractor_rejects_empty_or_invalid_responses(response):
    with pytest.raises(AttributeExtractionResponseError):
        extract_coordination_attributes("robots coordinate", client=lambda _req: response)


def test_extractor_wraps_provider_exception():
    def failing_client(_request):
        raise RuntimeError("provider down")

    with pytest.raises(AttributeExtractionError, match="provider failure"):
        extract_coordination_attributes("robots coordinate", client=failing_client)


def test_extractor_without_client_fails_visibly_when_no_api_key(monkeypatch):
    monkeypatch.delenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_API_KEY", raising=False)
    monkeypatch.delenv("TELLME_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(AttributeExtractionError, match="requires an API key"):
        extract_coordination_attributes("robots coordinate")


def test_openrouter_extractor_client_uses_selected_runtime_configuration(monkeypatch):
    monkeypatch.setenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_API_KEY", "selected-ui-key")
    monkeypatch.setenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_MODEL", "z-ai/glm-5.2")

    client = _build_default_client("z-ai/glm-5.2")

    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.api_key == "selected-ui-key"
    assert client.base_url == "https://openrouter.ai/api/v1"
    assert client.model == "z-ai/glm-5.2"


def test_openrouter_http_request_sends_bearer_authorization_and_model(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": json.dumps({"ok": True})}}]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        base_url="https://openrouter.ai/api/v1",
        model="z-ai/glm-5.2",
        api_key="selected-ui-key",
        timeout_seconds=17,
    )

    assert client.complete_json("return JSON") == {"ok": True}
    assert captured == {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "authorization": "Bearer selected-ui-key",
        "payload": {
            "model": "z-ai/glm-5.2",
            "messages": [
                {"role": "system", "content": "Return only valid JSON matching the requested schema."},
                {"role": "user", "content": "return JSON"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        "timeout": 17,
    }


def test_openrouter_http_error_logs_sanitized_json_and_preserves_exception(monkeypatch, capsys):
    api_key = "sk-or-v1-secret-value"
    error_body = json.dumps({
        "error": {
            "message": f"Key limit exceeded for {api_key}",
            "code": 403,
            "authorization": f"Bearer {api_key}",
        }
    }).encode("utf-8")
    expected_error = urllib.error.HTTPError(
        "https://openrouter.ai/api/v1/chat/completions",
        403,
        "Forbidden",
        hdrs={},
        fp=io.BytesIO(error_body),
    )

    def fake_urlopen(_request, timeout):
        assert timeout == 17
        raise expected_error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        base_url="https://openrouter.ai/api/v1",
        model="z-ai/glm-5.2",
        api_key=api_key,
        timeout_seconds=17,
    )

    with pytest.raises(urllib.error.HTTPError) as caught:
        client.complete_json("return JSON")

    assert caught.value is expected_error
    diagnostic = capsys.readouterr().err
    assert "TRACEFIX PROVIDER ERROR" in diagnostic
    assert "Provider: OpenRouter" in diagnostic
    assert "HTTP Status: 403" in diagnostic
    assert "Endpoint: https://openrouter.ai/api/v1/chat/completions" in diagnostic
    assert "Model: z-ai/glm-5.2" in diagnostic
    assert "Key limit exceeded for [REDACTED]" in diagnostic
    assert '"authorization": "[REDACTED]"' in diagnostic
    assert api_key not in diagnostic
    assert client.last_request_metadata["provider_name"] == "OpenRouter"
    assert client.last_request_metadata["model"] == "z-ai/glm-5.2"
    assert client.last_request_metadata["http_status"] == 403
    assert client.last_request_metadata["error"] == "http_error_403"


def test_openrouter_http_error_logs_sanitized_non_json_body(monkeypatch, capsys):
    api_key = "sk-or-v1-secret-value"
    expected_error = urllib.error.HTTPError(
        "https://openrouter.ai/api/v1/chat/completions",
        429,
        "Too Many Requests",
        hdrs={},
        fp=io.BytesIO(f"limit reached; Authorization: Bearer {api_key}".encode("utf-8")),
    )

    def fake_urlopen(_request, timeout):
        assert timeout == 120
        raise expected_error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        base_url="https://openrouter.ai/api/v1",
        model="z-ai/glm-5.2",
        api_key=api_key,
    )

    with pytest.raises(urllib.error.HTTPError):
        client.complete_json("return JSON")

    diagnostic = capsys.readouterr().err
    assert "limit reached" in diagnostic
    assert "Authorization: [REDACTED]" in diagnostic
    assert api_key not in diagnostic
