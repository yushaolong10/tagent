import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("MODEL_ID", "deepseek-v4-flash")

import agent
import context
from config import (DEFAULT_MAX_TOKENS, ESCALATED_MAX_TOKENS,
                    MODEL_MAX_OUTPUT_TOKENS)
from working_state import WorkingState


class ContextBudgetTests(unittest.TestCase):
    def test_multilingual_estimate_does_not_unicode_escape(self):
        chinese = context.estimate_tokens("汉" * 100_000)
        self.assertGreater(chinese, 60_000)
        self.assertLess(chinese, 70_000)

        english = context.estimate_tokens("a" * 100_000)
        self.assertGreater(english, 30_000)
        self.assertLess(english, 35_000)

    def test_output_budget_is_clamped_to_model_limit(self):
        result = context.fit_max_tokens([], "", [], 999_999)
        self.assertEqual(result, MODEL_MAX_OUTPUT_TOKENS)

    def test_output_budget_rejects_context_with_no_safe_output_room(self):
        huge_message = [{"role": "user", "content": "汉" * 1_600_000}]
        with self.assertRaisesRegex(ValueError, "context_length_exceeded"):
            context.fit_max_tokens(huge_message, "", [], DEFAULT_MAX_TOKENS)


class SafeCompactionTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {"role": "user", "content": "initial goal"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "old follow-up"},
            {"role": "assistant", "content": "recent answer"},
            {"role": "user", "content": "recent question"},
        ]

    @patch("context.write_transcript")
    @patch("context.summarize_history", return_value="structured checkpoint")
    def test_compaction_keeps_recent_messages(self, summarize, transcript):
        result = context.compact_history(self.messages, keep_messages=2)
        self.assertIn("structured checkpoint", result[0]["content"])
        self.assertEqual(result[1:], self.messages[-2:])
        summarize.assert_called_once_with(self.messages[:-2])

    @patch("context.write_transcript")
    @patch("context.summarize_history", side_effect=RuntimeError("API down"))
    def test_failed_summary_keeps_original_history(self, summarize, transcript):
        result = context.compact_history(self.messages, keep_messages=2)
        self.assertIs(result, self.messages)

    def test_recent_tail_does_not_split_tool_pair(self):
        messages = [
            {"role": "user", "content": "goal"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file",
                 "input": {"path": "x"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "data"}]},
            {"role": "assistant", "content": "done"},
        ]
        self.assertEqual(context.recent_tail_start(messages, 2), 1)


class TruncationRecoveryTests(unittest.TestCase):
    @patch("agent.prepare_context", side_effect=lambda messages, **kwargs: messages)
    @patch("agent.assemble_tool_pool", return_value=([], {}))
    @patch("agent.trigger_hooks")
    @patch("agent.call_llm")
    def test_partial_text_is_kept_before_continuation(
            self, call_llm, trigger_hooks, tool_pool, prepare):
        call_llm.side_effect = [
            SimpleNamespace(
                stop_reason="max_tokens",
                content=[SimpleNamespace(type="text", text="partial")]),
            SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="finished")]),
        ]
        messages = [{"role": "user", "content": "write something"}]
        agent.agent_loop(messages, {}, WorkingState())

        self.assertEqual(call_llm.call_args_list[0].args[4], DEFAULT_MAX_TOKENS)
        self.assertEqual(call_llm.call_args_list[1].args[4],
                         ESCALATED_MAX_TOKENS)
        self.assertEqual(messages[1]["content"][0].text, "partial")
        self.assertEqual(messages[2]["role"], "user")
        self.assertEqual(messages[3]["content"][0].text, "finished")

    @patch("agent.prepare_context", side_effect=lambda messages, **kwargs: messages)
    @patch("agent.trigger_hooks")
    @patch("agent.call_llm")
    def test_truncated_tool_call_receives_result_before_next_request(
            self, call_llm, trigger_hooks, prepare):
        tool_block = SimpleNamespace(
            type="tool_use", id="tool_1", name="read_file",
            input={"path": "README.md"})
        call_llm.side_effect = [
            SimpleNamespace(stop_reason="max_tokens", content=[tool_block]),
            SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="done")]),
        ]
        tool_pool = ([], {"read_file": lambda path: "file contents"})
        messages = [{"role": "user", "content": "inspect the readme"}]

        with patch("agent.assemble_tool_pool", return_value=tool_pool):
            agent.agent_loop(messages, {}, WorkingState())

        self.assertEqual(messages[2]["role"], "user")
        self.assertEqual(messages[2]["content"][0]["type"], "tool_result")
        self.assertEqual(messages[2]["content"][0]["tool_use_id"], "tool_1")
        self.assertEqual(call_llm.call_args_list[1].args[4],
                         ESCALATED_MAX_TOKENS)


if __name__ == "__main__":
    unittest.main()
