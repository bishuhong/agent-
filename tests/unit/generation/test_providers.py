import json
from uuid import uuid4

import httpx
import pytest

from etl_agent.adapters.models.providers import ModelProviderRouter, safe_prompt
from etl_agent.adapters.workflow.langgraph import LangGraphWorkflow
from etl_agent.config import Settings
from etl_agent.modules.generation.ports import ClarificationOutput, ModelContext
from etl_agent.modules.pipelines.service import artifacts_for
from etl_agent.shared.canonical import artifact_package
from etl_agent.shared.errors import ApplicationError


def configured_settings(provider="qwen"):
    return Settings(
        environment="test",
        model_providers={
            provider: {
                "base_url": "https://models.test/v1",
                "api_key": "provider-api-key-never-persist",
                "model": "configured-test-model",
                "prompt_version": "prompt-v1",
                "policy_version": "policy-v1",
            }
        },
    )


def context(binding):
    return ModelContext(
        prompt="move source to target",
        provider_binding=binding,
        source_profiles=[{"id": str(uuid4()), "resource_name": "source", "fields": []}],
        target_profile={"id": str(uuid4()), "resource_name": "target", "fields": []},
    )


@pytest.mark.parametrize("provider", ["openai_compatible", "deepseek", "qwen"])
def test_three_managed_providers_share_typed_transport(provider):
    settings = configured_settings(provider)
    seen = []

    def handle(request):
        seen.append(request)
        assert str(request.url) == "https://models.test/v1/chat/completions"
        data = json.loads(request.content)
        assert data["model"] == "configured-test-model"
        assert "tools" not in data
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "clarification",
                                    "questions": [
                                        {
                                            "question_key": "target",
                                            "question": "Which target field?",
                                        }
                                    ],
                                }
                            )
                        },
                    }
                ]
            },
        )

    router = ModelProviderRouter(settings, httpx.MockTransport(handle))
    binding = router.resolve_binding(provider)
    output = router.generate(binding, context(binding))
    assert isinstance(output, ClarificationOutput)
    assert len(seen) == 1
    assert (
        "provider-api-key-never-persist"
        not in json.loads(seen[0].content)["messages"][1]["content"]
    )


def test_no_provider_configuration_fails_closed():
    with pytest.raises(ApplicationError) as error:
        ModelProviderRouter(Settings()).resolve_binding(None)
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "message",
    [
        {"content": "not JSON"},
        {"content": '{"kind":"candidate"}'},
        {"content": "{}", "tool_calls": [{"function": {"name": "execute_etl"}}]},
        {
            "content": json.dumps(
                {
                    "kind": "clarification",
                    "questions": [{"question_key": "q", "question": "password=do-not-store"}],
                }
            )
        },
        {
            "content": json.dumps(
                {
                    "kind": "clarification",
                    "questions": [
                        {"question_key": "q", "question": "one"},
                        {"question_key": "q", "question": "two"},
                    ],
                }
            )
        },
    ],
)
def test_untrusted_model_output_rejected_before_checkpoint(message):
    router = ModelProviderRouter(
        configured_settings(),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"choices": [{"finish_reason": "stop", "message": message}]}
            )
        ),
    )
    binding = router.resolve_binding(None)
    with pytest.raises(ApplicationError) as error:
        router.generate(binding, context(binding))
    assert error.value.code == "VALIDATION_FAILED"
    assert "do-not-store" not in error.value.message


def test_provider_failure_is_safe_and_does_not_change_provider():
    def fail(request):
        raise httpx.ReadTimeout("unsafe-provider-error-contents", request=request)

    router = ModelProviderRouter(configured_settings(), httpx.MockTransport(fail))
    binding = router.resolve_binding(None)
    with pytest.raises(ApplicationError) as error:
        router.generate(binding, context(binding))
    assert error.value.status_code == 503
    assert "unsafe-provider-error-contents" not in error.value.message


def test_provider_binding_drift_rejected():
    settings = configured_settings()
    router = ModelProviderRouter(settings)
    binding = router.resolve_binding(None)
    settings.model_providers["qwen"]["model"] = "changed-model"
    with pytest.raises(ApplicationError) as error:
        router.generate(binding, context(binding))
    assert error.value.code == "FINGERPRINT_CHANGED"


def test_prompt_credentials_redacted_before_storage():
    cleaned = safe_prompt('move orders; password="do-not-store"; token=also-secret Bearer abc.def')
    assert (
        "do-not-store" not in cleaned and "also-secret" not in cleaned and "abc.def" not in cleaned
    )


def test_workflow_requires_real_postgres_configuration():
    workflow = LangGraphWorkflow(Settings(), ModelProviderRouter(Settings()))
    with pytest.raises(ApplicationError) as error:
        workflow.ensure_ready()
    assert error.value.status_code == 503


def test_complete_artifact_package_hashes_raw_hocon_and_typed_materials():
    plan = {"nodes": [], "edges": [], "quality_contract": {}, "supervision_contract": {}}
    first, hashes = artifact_package(artifacts_for(plan, "env { a=1 }\n"))
    second, _ = artifact_package(artifacts_for(plan, "env { a=1 }"))
    assert first != second
    assert set(hashes) == {"etl_plan", "hocon", "dag", "quality_contract", "supervision_contract"}
