"""AnthropicLLM con un cliente falso: verifica el request sin gastar API."""
from types import SimpleNamespace as NS

import pytest

from qa.agents.llm import AnthropicLLM, LLMError, LLMRefusal


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, messages):
        self.queue = list(messages)
        self.calls = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        return FakeStream(self.queue.pop(0))


def msg(stop="end_turn", text='{"ok": true}', searches=0, extra=()):
    usage = NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
               server_tool_use=NS(web_search_requests=searches))
    content = list(extra) + ([NS(type="text", text=text)] if text is not None else [])
    return NS(stop_reason=stop, content=content, usage=usage, model="claude-sonnet-5-5", stop_details=NS(category="x"))


def call(llm, model="claude-sonnet-5-5", ws=0):
    return llm.complete(agent="a", model=model, system="sys", user="u", schema={"type": "object"}, effort="medium",
                        max_tokens=1000, web_search_uses=ws)


def test_request_shape_and_cost():
    fc = FakeClient([msg(searches=2, extra=[NS(type="web_search_tool_result", content=[NS(url="https://x.com/a")])])])
    r = call(AnthropicLLM(client=fc), ws=3)
    kw = fc.calls[0]
    assert kw["output_config"]["format"]["type"] == "json_schema" and kw["output_config"]["effort"] == "medium"
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
    assert kw["tools"][0]["type"] == "web_search_20260209" and kw["tools"][0]["max_uses"] == 3
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in kw and "temperature" not in kw
    assert r.data == {"ok": True} and r.web_sources == ["https://x.com/a"]
    assert r.cost_usd == pytest.approx((1000 * 2 + 200 * 10) / 1e6 + 0.02)


def test_haiku_has_no_fallback():
    fc = FakeClient([msg()])
    call(AnthropicLLM(client=fc), model="claude-haiku-5-5")
    assert "fallbacks" not in fc.calls[0] and fc.calls[0]["betas"] == []


def test_pause_turn_continues():
    fc = FakeClient([msg(stop="pause_turn", text=None), msg()])
    r = call(AnthropicLLM(client=fc))
    assert len(fc.calls) == 2 and fc.calls[1]["messages"][-1]["role"] == "assistant"
    assert r.input_tokens == 2000


def test_refusal_and_truncation():
    with pytest.raises(LLMRefusal):
        call(AnthropicLLM(client=FakeClient([msg(stop="refusal", text=None)])))
    with pytest.raises(LLMError):
        call(AnthropicLLM(client=FakeClient([msg(stop="max_tokens")])))
    with pytest.raises(LLMError):
        call(AnthropicLLM(client=FakeClient([msg(text="no json")])))
