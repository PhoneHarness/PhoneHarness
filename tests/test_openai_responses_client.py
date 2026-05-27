import unittest
from json import loads
from unittest.mock import patch

from phoneharness.model.client import (
    OpenAICompatClient,
    _chat_payload_to_responses_payload,
    _responses_payload_to_chat_payload,
)


class OpenAIResponsesClientTest(unittest.TestCase):
    def test_responses_url_from_v1_base(self):
        client = OpenAICompatClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-4.1",
            api_format="responses",
        )

        self.assertEqual(client._responses_url(), "https://api.openai.com/v1/responses")

    def test_infers_responses_format_from_responses_endpoint(self):
        client = OpenAICompatClient(
            base_url="https://api.openai.com/v1/responses",
            api_key="test",
            model="gpt-4.1",
        )

        self.assertEqual(client.api_format, "responses")

    def test_client_posts_to_responses_endpoint_and_normalizes_response(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def read(self):
                return (
                    b'{"id":"resp_1","model":"gpt-4.1","output":['
                    b'{"type":"message","content":[{"type":"output_text","text":"ok"}]}'
                    b'],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}'
                )

        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["body"] = loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        client = OpenAICompatClient(
            base_url="https://api.openai.com/v1",
            api_key="test",
            model="gpt-4.1",
            api_format="responses",
        )

        with patch("phoneharness.model.client.request.urlopen", fake_urlopen):
            result = client.chat_completion(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(captured["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(captured["body"]["input"][0]["content"], "hi")
        self.assertEqual(result.response_payload["choices"][0]["message"]["content"], "ok")

    def test_converts_chat_tools_and_tool_messages_to_responses_payload(self):
        payload = _chat_payload_to_responses_payload(
            {
                "model": "gpt-4.1",
                "messages": [
                    {"role": "system", "content": "You are a phone agent."},
                    {"role": "user", "content": "open wifi"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "open_wifi", "arguments": "{}"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "name": "open_wifi",
                        "content": '{"ok": true}',
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "open_wifi",
                            "description": "Open Wi-Fi settings.",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "open_wifi"}},
                "temperature": 0,
                "max_tokens": 128,
            }
        )

        self.assertEqual(payload["instructions"], "You are a phone agent.")
        self.assertEqual(payload["max_output_tokens"], 128)
        self.assertEqual(payload["tools"][0]["name"], "open_wifi")
        self.assertEqual(payload["tool_choice"], {"type": "function", "name": "open_wifi"})
        self.assertEqual(payload["input"][1]["type"], "function_call")
        self.assertEqual(payload["input"][2]["type"], "function_call_output")

    def test_converts_responses_function_call_to_chat_completion_shape(self):
        converted = _responses_payload_to_chat_payload(
            {
                "id": "resp_123",
                "model": "gpt-4.1",
                "created_at": 123,
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_abc",
                        "name": "open_wifi",
                        "arguments": "{}",
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
            }
        )

        choice = converted["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["tool_calls"][0]["id"], "call_abc")
        self.assertEqual(choice["message"]["tool_calls"][0]["function"]["name"], "open_wifi")
        self.assertEqual(converted["usage"]["prompt_tokens"], 10)
        self.assertEqual(converted["usage"]["completion_tokens"], 3)

    def test_converts_responses_message_text_to_chat_completion_shape(self):
        converted = _responses_payload_to_chat_payload(
            {
                "id": "resp_456",
                "model": "gpt-4.1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
            }
        )

        choice = converted["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(choice["message"]["content"], "done")


if __name__ == "__main__":
    unittest.main()
