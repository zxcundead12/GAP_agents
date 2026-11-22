"""Mock HTTP only; no real provider, key, .env, COM, or GAP access."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from gap_agent.llm import CompatibleLLM, LLMError


class LLMRobustnessTests(unittest.TestCase):
    def invoke(self, data, config=None):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(data).encode()
        opener = MagicMock()
        opener.open.return_value = response
        client = CompatibleLLM(config if config is not None else {
            'model': 'synthetic-model', 'api_key_env': 'SYNTHETIC_API_KEY',
        })
        with patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'mock-only-api-key'}, clear=True), patch(
                'gap_agent.llm.request.build_opener', return_value=opener):
            value = client.parse('Синтетический запрос', {})
        return value, client

    def test_malformed_response_envelopes_are_classified_without_attribute_errors(self):
        for value in ({'choices': None}, {'choices': []}, {'choices': [None]}, {'choices': ['bad']},
                      {'choices': [{'finish_reason': 'stop', 'message': []}]},
                      {'choices': [{'finish_reason': 'stop', 'message': None}]},
                      {'choices': [{'finish_reason': 'stop', 'message': {'content': {}}}]}):
            with self.subTest(value=value), self.assertRaises(LLMError) as caught:
                self.invoke(value)
            self.assertEqual(caught.exception.status, 'LLM_INVALID_OUTPUT')

    def test_invalid_config_types_and_urls_fail_before_network(self):
        cases = [[], {'base_url': []}, {'base_url': 'https://example.test:bad/v1'},
                 {'api_key_env': []}, {'api_key_env': 'INVALID\nNAME'}, {'model': []},
                 {'model': '   '}, {'model': 'synthetic', 'timeout_seconds': True},
                 {'model': 'synthetic', 'response_format': 'invalid'}]
        with patch.dict(os.environ, {}, clear=True), patch('gap_agent.llm.request.build_opener') as opener:
            for config in cases:
                with self.subTest(config=config), self.assertRaises(LLMError) as caught:
                    CompatibleLLM(config).parse('test', {})
                self.assertEqual(caught.exception.status, 'LLM_NOT_CONFIGURED')
        opener.assert_not_called()

    def test_unconfigured_groq_is_explicit_without_a_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch('gap_agent.llm.request.build_opener') as opener:
            client = CompatibleLLM({'base_url': 'https://api.groq.com/openai/v1'})
            with self.assertRaises(LLMError) as caught:
                client.parse('test', {})
        self.assertEqual(caught.exception.status, 'LLM_NOT_CONFIGURED')
        self.assertFalse(client.metadata['called'])
        opener.assert_not_called()

    def test_missing_settings_message_lists_only_missing_values(self):
        with patch.dict(os.environ, {}, clear=True), patch('gap_agent.llm.request.build_opener') as opener:
            with self.assertRaises(LLMError) as caught:
                CompatibleLLM({'model': 'synthetic-model', 'api_key_env': 'SYNTHETIC_API_KEY'}).parse('test', {})
        self.assertIn('SYNTHETIC_API_KEY', str(caught.exception))
        self.assertNotIn('GAP_LLM_MODEL', str(caught.exception))
        opener.assert_not_called()

    def test_usage_metadata_is_numeric_allowlisted_and_response_is_hashed(self):
        value, client = self.invoke({
            'id': {'debug': 'UNTRUSTED_RESPONSE_ID'},
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"operation":"inspect"}'}}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7,
                      'debug': 'UNTRUSTED_USAGE_SECRET', 'extra': {'secret': 'UNTRUSTED_SECRET'}},
        })
        self.assertEqual(value, {'operation': 'inspect'})
        self.assertEqual(client.metadata['usage'], {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7})
        self.assertIsNone(client.metadata['response_id'])
        self.assertEqual(len(client.metadata['response_sha256']), 64)
        self.assertIn('elapsed_seconds', client.metadata)
        self.assertNotIn('UNTRUSTED_', json.dumps(client.metadata))

    def test_invalid_unicode_and_deep_json_are_rejected_before_logs(self):
        for content in ('{"value":"\\ud800"}', '[' * 1500 + '0' + ']' * 1500):
            with self.subTest(content_length=len(content)), self.assertRaises(LLMError) as caught:
                self.invoke({'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})
            self.assertEqual(caught.exception.status, 'LLM_INVALID_OUTPUT')

    def test_http_failure_keeps_elapsed_trace_and_omits_provider_error_body(self):
        client = CompatibleLLM({'model': 'synthetic-model', 'api_key_env': 'SYNTHETIC_API_KEY'})
        opener = MagicMock()
        opener.open.side_effect = HTTPError('https://example.test', 429, 'SECRET_PROVIDER_DETAIL', {}, None)
        with patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'mock-only-api-key'}, clear=True), patch(
                'gap_agent.llm.request.build_opener', return_value=opener), self.assertRaises(LLMError) as caught:
            client.parse('test', {})
        self.assertEqual(caught.exception.status, 'LLM_UNAVAILABLE')
        self.assertNotIn('SECRET_PROVIDER_DETAIL', str(caught.exception))
        self.assertIn('elapsed_seconds', client.metadata)
        self.assertTrue(client.metadata['called'])


if __name__ == '__main__':
    unittest.main()
