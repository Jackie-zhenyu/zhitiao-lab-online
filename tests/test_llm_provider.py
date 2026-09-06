import json
import logging

import httpx
import pytest
from pydantic import ValidationError

from agent.config import LLMSettings, read_settings
from agent.provider import ProviderError, QwenProvider
from agent_fixtures import settings


def transport(monkeypatch,handler):
    client = httpx.Client
    class TestClient(client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)
    # SDK 检查 httpx.Client 类型；保留类语义，仅替换网络传输。
    monkeypatch.setattr("agent.provider.httpx.Client", TestClient)


def test_config_environment_precedence_no_interpolation_and_no_secret_serialization(tmp_path,monkeypatch):
    (tmp_path/".env").write_text("LLM_PROVIDER=qwen\nLLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\nLLM_MODEL=qwen-plus\nLLM_API_KEY=${NOT_EXPANDED}\n",encoding="utf-8")
    monkeypatch.delenv("LLM_API_KEY")
    config,status = read_settings(tmp_path)
    assert config.ready and config.api_key.get_secret_value()=="${NOT_EXPANDED}"
    assert "NOT_EXPANDED" not in config.model_dump_json()+repr(config)+status
    monkeypatch.setenv("LLM_MODEL","another-configured-model")
    assert read_settings(tmp_path)[0].model=="another-configured-model"
    monkeypatch.setenv("LLM_API_KEY","")
    config,status = read_settings(tmp_path)
    assert config is None and "LLM_API_KEY" in status


@pytest.mark.parametrize("url",["http://dashscope.aliyuncs.com/compatible-mode/v1","https://evil.example/compatible-mode/v1","https://key@dashscope.aliyuncs.com/compatible-mode/v1","https://dashscope.aliyuncs.com/compatible-mode/v1?key=secret","https://dashscope.aliyuncs.com/compatible-mode/v1#secret","https://dashscope.aliyuncs.com.evil.example/compatible-mode/v1"])
def test_unsafe_destination_rejected(url):
    with pytest.raises(ValidationError):
        LLMSettings(provider="qwen",base_url=url,model="qwen-plus")


def test_actual_sdk_request_contains_tools_and_configured_model(monkeypatch):
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={"id":"mock","choices":[{"index":0,"finish_reason":"tool_calls","message":{"role":"assistant","content":None,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"profile_data","arguments":"{}"}}]}}]})
    transport(monkeypatch,respond)
    config = settings()
    reply = QwenProvider(config).complete([{"role":"user","content":"test-only"}],[{"type":"function","function":{"name":"profile_data","parameters":{"type":"object"}}}],timeout_s=2)
    body = json.loads(seen[0].content)
    assert str(seen[0].url)==config.base_url+"/chat/completions"
    assert body["model"]==config.model and body["tools"][0]["function"]["name"]=="profile_data"
    assert body["tool_choice"]=="auto" and body["enable_thinking"] is False and body["stream"] is False
    assert reply.tool_calls[0].name=="profile_data"
    assert seen[0].headers["authorization"]=="Bearer "+config.api_key.get_secret_value()


@pytest.mark.parametrize("status,code",[(401,"authentication"),(403,"authentication"),(429,"rate_limit"),(500,"server_error"),(400,"request_rejected"),(302,"redirect_blocked")])
def test_sdk_errors_are_redacted_and_no_automatic_retry_or_redirect(monkeypatch,status,code):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status,headers={"location":"https://evil.example/steal"},json={"error":{"message":"API-SECRET full sensitive log"}})
    transport(monkeypatch,respond)
    with pytest.raises(ProviderError) as error:
        QwenProvider(settings()).complete([],[],timeout_s=2)
    assert error.value.code==code and len(requests)==1
    assert "SECRET" not in str(error.value) and "sensitive log" not in str(error.value)


def test_sdk_timeout_and_invalid_response_are_safe(monkeypatch):
    def fail(request):
        raise httpx.ReadTimeout("private-key in failed request",request=request)
    transport(monkeypatch,fail)
    with pytest.raises(ProviderError) as error:
        QwenProvider(settings()).complete([],[],timeout_s=.01)
    assert error.value.code=="timeout" and "private-key" not in str(error.value)


def test_missing_key_does_not_initialize_network():
    config = LLMSettings(provider="qwen",base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",model="qwen-plus")
    with pytest.raises(ProviderError) as error:
        QwenProvider(config)
    assert error.value.code=="configuration"


def test_sdk_debug_logs_do_not_capture_prompt_response_header_or_exception(monkeypatch,caplog):
    caplog.set_level(logging.DEBUG)
    def fail(request):
        raise httpx.ReadTimeout("API-SECRET private failed request",request=request)
    transport(monkeypatch,fail)
    with pytest.raises(ProviderError):
        QwenProvider(settings()).complete([{"role":"user","content":"PRIVATE-PROMPT"}],[],timeout_s=.01)
    assert "PRIVATE-PROMPT" not in caplog.text and "API-SECRET" not in caplog.text
    assert "TEST-ONLY-NOT-A-REAL-KEY" not in caplog.text


@pytest.mark.parametrize("body",[{}, {"choices":[]}, {"choices":[{"index":0,"finish_reason":"length","message":{"role":"assistant","content":"truncated"}}]}])
def test_invalid_or_truncated_sdk_response_cannot_be_a_valid_answer(monkeypatch,body):
    transport(monkeypatch,lambda request:httpx.Response(200,json=body))
    with pytest.raises(ProviderError) as error:
        QwenProvider(settings()).complete([],[],timeout_s=2)
    assert error.value.code=="invalid_response"
