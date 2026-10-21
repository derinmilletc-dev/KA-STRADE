"""Kais AI akıl yürütme/bağlam iyileştirmeleri: geçmiş, hızlı yol takibi, düşünme seviyesi, sesli giriş sınırı, prompt."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx

from app.assistant_config import AssistantConfig
from app.assistant_fastpath import answer, intent, is_followup
from app.assistant_llm import VOICE_STYLE_EN, VOICE_STYLE_TR, AnthropicTransport, build_request, needs_deep_reasoning
from app.assistant_models import ChatInput
from app.assistant_prompt import system_prompt


class ConfigDefaultsTests(unittest.TestCase):
    def test_budget_and_quota_limits_are_unchanged_and_history_is_eight(self):
        config = AssistantConfig()
        self.assertEqual(config.history_messages, 8)
        self.assertEqual(config.daily_limit, 20)
        self.assertEqual(config.monthly_budget_usd, 50)
        self.assertEqual(config.model, "claude-haiku-5-5")
        self.assertEqual(config.voice_max_input_chars, 800)
        self.assertEqual(config.max_input_chars, 500)


class FollowupRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_short_followups_are_detected(self):
        for message in ("peki ya ETH?", "Ya 4 saatlik?", "peki kredim?", "and the 4h?", "what about ETH?", "bunu neden söyledin", "same for SOL"):
            with self.subTest(message=message):
                self.assertTrue(is_followup(message))

    def test_standalone_questions_are_not_followups(self):
        for message in ("kredim ne kadar", "what is my plan", "how many credits do I have", "planım ne", "günlük limitim kaç"):
            with self.subTest(message=message):
                self.assertFalse(is_followup(message))

    async def test_followup_with_history_goes_to_model_but_standalone_keeps_fast_path(self):
        tools = SimpleNamespace(config=AssistantConfig())
        self.assertEqual(intent("peki kredim?"), "get_my_credits")
        self.assertIsNone(await answer(tools, "u", "peki kredim?", "tr", has_history=True))
        tools.dispatch = AsyncMock(return_value={"data": {"unlimited": True}, "fetched_at": "x", "stale": False})
        quick = await answer(tools, "u", "peki kredim?", "tr", has_history=False)
        self.assertIsNotNone(quick)
        quick = await answer(tools, "u", "kredim ne kadar", "tr", has_history=True)
        self.assertIsNotNone(quick)

    async def test_support_routing_is_never_skipped_by_followup_rule(self):
        tools = SimpleNamespace(config=AssistantConfig())
        quick = await answer(tools, "u", "peki ödeme yaptım ama aktif olmadı", "tr", has_history=True)
        self.assertIsNotNone(quick)
        self.assertEqual(quick["sources"], [])


class EffortTests(unittest.IsolatedAsyncioTestCase):
    def test_deep_reasoning_only_for_analysis_style_questions(self):
        for message in ("BTC analizini yorumla", "Why is ETH risky right now?", "iki senaryoyu karşılaştır", "x" * 200):
            with self.subTest(message=message[:30]):
                self.assertTrue(needs_deep_reasoning(message))
        for message in ("merhaba", "kredim ne kadar", "how do I connect my API key?", "en mantıklı coin hangisi", "which one is cheaper"):
            with self.subTest(message=message):
                self.assertFalse(needs_deep_reasoning(message))

    def test_request_effort_follows_question_and_config(self):
        config = AssistantConfig()
        self.assertEqual(build_request(config, "tr", ChatInput(message="merhaba")).effort, "low")
        self.assertEqual(build_request(config, "tr", ChatInput(message="BTC için riskleri değerlendir")).effort, "medium")
        high = AssistantConfig(deep_effort="high")
        self.assertEqual(build_request(high, "tr", ChatInput(message="BTC için riskleri değerlendir")).effort, "high")
        low = AssistantConfig(deep_effort="low")
        self.assertEqual(build_request(low, "tr", ChatInput(message="BTC için riskleri değerlendir")).effort, "low")

    def test_hard_questions_route_to_deep_model_and_simple_ones_stay_on_base_model(self):
        config = AssistantConfig()
        simple = build_request(config, "tr", ChatInput(message="merhaba"))
        deep = build_request(config, "tr", ChatInput(message="BTC için riskleri değerlendir"))
        self.assertEqual((simple.model, simple.deep, simple.max_tokens), ("claude-haiku-5-5", False, config.max_output_tokens))
        self.assertEqual((deep.model, deep.deep, deep.max_tokens), ("claude-opus-5-5", True, config.deep_max_output_tokens))
        single = AssistantConfig(deep_model="")
        self.assertEqual(build_request(single, "tr", ChatInput(message="BTC için riskleri değerlendir")).model, "claude-haiku-5-5")

    def test_cost_uses_prices_of_the_model_actually_called(self):
        config = AssistantConfig()
        self.assertEqual(config.prices_for("claude-haiku-5-5")[1], config.output_price_usd_per_million)
        self.assertEqual(config.prices_for("claude-opus-5-5"), (
            config.deep_input_price_usd_per_million, config.deep_output_price_usd_per_million,
            config.deep_cache_write_price_usd_per_million, config.deep_cache_read_price_usd_per_million))

    async def test_conversation_loop_keeps_deep_model_and_effort_on_every_call(self):
        from app.assistant_llm import run_conversation
        from app.assistant_models import ModelAnswer
        config = AssistantConfig()
        seen = []

        async def paid(request, first):
            seen.append((request.model, request.effort, request.max_tokens))
            return ModelAnswer("Değerlendirme hazır.", 10, 10, 0, 0, ())

        await run_conversation(config, "tr", ChatInput(message="BTC için riskleri değerlendir"), "u", None, paid, AsyncMock(return_value=10))
        self.assertEqual(seen, [("claude-opus-5-5", config.deep_effort, config.deep_max_output_tokens)])

    def test_history_is_passed_in_order_with_current_message_last(self):
        payload = ChatInput(message="peki ya ETH?", history=[{"role": "user", "content": "BTC nasıl?"}, {"role": "assistant", "content": "Doğrulanamadı."}])
        messages = build_request(AssistantConfig(), "tr", payload).messages
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        self.assertTrue(messages[-1]["content"].startswith("peki ya ETH?"))

    async def _transport(self, side_effect, effort):
        config = AssistantConfig(ANTHROPIC_API_KEY="test-key")
        request = build_request(config, "tr", ChatInput(message="analiz"))
        request = request.__class__(request.system, request.tools, request.messages, request.max_tokens, request.payload, effort)
        create = AsyncMock(side_effect=side_effect)
        client = MagicMock()
        client.messages.create = create
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.assistant_llm.anthropic.AsyncAnthropic", return_value=client):
            answer_ = await AnthropicTransport(config).generate(request)
        return create, answer_

    async def test_medium_effort_is_sent_and_rejection_falls_back_to_low_on_same_model(self):
        ok = SimpleNamespace(content=[SimpleNamespace(type="text", text="tamam")], usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        create, result = await self._transport([ok], "medium")
        self.assertEqual(create.await_args.kwargs["output_config"], {"effort": "medium"})
        self.assertEqual(result.reply, "tamam")
        rejection = anthropic.BadRequestError("bad", response=httpx.Response(400, request=httpx.Request("POST", "http://x")), body=None)
        create, result = await self._transport([rejection, ok], "medium")
        self.assertEqual([c.kwargs["output_config"]["effort"] for c in create.await_args_list], ["medium", "low"])
        self.assertEqual({c.kwargs["model"] for c in create.await_args_list}, {"claude-haiku-5-5"})
        self.assertEqual(result.reply, "tamam")


class VoiceAndPromptTests(unittest.TestCase):
    def test_voice_style_allows_five_sentences_for_complex_questions(self):
        self.assertIn("5 cümle", VOICE_STYLE_TR)
        self.assertIn("at most 5", VOICE_STYLE_EN)

    def test_prompt_has_reasoning_rules_and_keeps_safety_rules(self):
        for language, reasoning, safety in (
            ("tr", ("Akıl yürütme ve bağlam", "sırf hoşuna gitsin", "çelişiyorsa", "tek kısa soruyla netleştir"), ("Entry/SL/TP", "işlem yapamaz", "get_protection_status")),
            ("en", ("Reasoning and context", "just to please", "contradict each other", "ask one short clarifying question"), ("Entry/SL/TP", "cannot place or trigger trades", "get_protection_status")),
        ):
            with self.subTest(language=language):
                prompt = system_prompt(language)
                for phrase in reasoning + safety:
                    self.assertIn(phrase, prompt)


if __name__ == "__main__":
    unittest.main()
