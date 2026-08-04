"""Context budget pipeline: compaction, transcripts, reactive recovery."""
from __future__ import annotations

import json, math, time, uuid
from pathlib import Path

from config import (client, MODEL, TRANSCRIPT_DIR, TOOL_RESULTS_DIR,
                    PERSIST_THRESHOLD, MODEL_CONTEXT_TOKENS,
                    MODEL_MAX_OUTPUT_TOKENS, CONTEXT_COMPACT_THRESHOLD,
                    CONTEXT_SAFETY_MARGIN, MIN_OUTPUT_TOKENS,
                    SUMMARY_MAX_TOKENS, SUMMARY_KEEP_MESSAGES)
from tools import extract_text


# ── Context Compaction ──

# Compaction is layered: first shrink oversized tool results, then trim old
# message ranges, and only call the model for a summary when the context is
# still too large or the model explicitly asks for compact.
def estimate_tokens(value) -> int:
    """Estimate tokens using DeepSeek's documented character ratios.

    English letters/whitespace use 0.3 token per character, Chinese characters
    use 0.6, and digits or symbols use 1.  A 10% margin absorbs normal tokenizer
    variation and the approximation involved in serializing SDK content blocks.
    """
    text = json.dumps(value, default=str, ensure_ascii=False)
    weighted = sum(_token_weight(char) for char in text)
    return max(1, math.ceil(weighted * 1.10))


def _token_weight(char: str) -> float:
    codepoint = ord(char)
    is_chinese = (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x323AF
    )
    if is_chinese:
        return 0.6
    if char.isascii() and (char.isalpha() or char.isspace()):
        return 0.3
    return 1.0


def estimate_size(messages: list) -> int:
    """Backward-compatible alias; the returned unit is now estimated tokens."""
    return estimate_tokens(messages)


def estimate_request_tokens(messages: list, system: str = "",
                            tools: list | None = None) -> int:
    return estimate_tokens({
        "system": system,
        "messages": messages,
        "tools": tools or [],
    })


def fit_max_tokens(messages: list, system: str, tools: list,
                   requested: int) -> int:
    input_tokens = estimate_request_tokens(messages, system, tools)
    available = MODEL_CONTEXT_TOKENS - input_tokens - CONTEXT_SAFETY_MARGIN
    if available < MIN_OUTPUT_TOKENS:
        raise ValueError(
            "context_length_exceeded: insufficient output budget after input")
    return min(requested, MODEL_MAX_OUTPUT_TOKENS, available)

def block_type(block):
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def message_has_tool_use(message: dict) -> bool:
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(block_type(block) == "tool_use" for block in content)


def is_tool_result_message(message: dict) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result"
               for block in content)


def collect_tool_results(messages: list):
    found = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if msg.get("role") != "user" or not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                found.append((mi, bi, block))
    return found


def persist_large_output(tool_use_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
    if not path.exists():
        path.write_text(output)
    return (f"<persisted-output>\nFull output: {path}\n"
            f"Preview:\n{output[:2000]}\n</persisted-output>")


def tool_result_budget(messages: list, max_bytes: int = 10_000) -> list:
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if last.get("role") != "user" or not isinstance(content, list):
        return messages
    blocks = [(i, b) for i, b in enumerate(content)
              if isinstance(b, dict) and b.get("type") == "tool_result"]
    total = sum(len(str(b.get("content", ""))) for _, b in blocks)
    if total <= max_bytes:
        return messages
    for _, block in sorted(blocks,
                           key=lambda pair: len(str(pair[1].get("content", ""))),
                           reverse=True):
        if total <= max_bytes:
            break
        text = str(block.get("content", ""))
        block["content"] = persist_large_output(
            block.get("tool_use_id", "unknown"), text)
        total = sum(len(str(b.get("content", ""))) for _, b in blocks)
    return messages


def snip_compact(messages: list, max_messages: int = 100) -> list:
    if len(messages) <= max_messages:
        return messages
    head_end, tail_start = 3, len(messages) - (max_messages - 3)
    if head_end > 0 and message_has_tool_use(messages[head_end - 1]):
        while head_end < len(messages) and is_tool_result_message(messages[head_end]):
            head_end += 1
    if (tail_start > 0 and tail_start < len(messages)
            and is_tool_result_message(messages[tail_start])
            and message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    if head_end >= tail_start:
        return messages
    snipped = tail_start - head_end
    return (messages[:head_end]
            + [{"role": "user", "content": f"[snipped {snipped} messages]"}]
            + messages[tail_start:])


def micro_compact(messages: list, history_limit: int = 2000) -> list:
    # Keep every tool result of the current round fully intact. Tool results
    # from earlier rounds are truncated to history_limit chars so the live
    # context stays small while the latest work stays completely visible.
    tool_results = collect_tool_results(messages)
    if not tool_results:
        return messages
    current_round = tool_results[-1][0]
    for mi, _, block in tool_results:
        if mi == current_round:
            continue
        text = str(block.get("content", ""))
        if len(text) > history_limit:
            block["content"] = text[:history_limit] + (
                f"\n[... truncated to {history_limit} chars; do NOT re-run; "
                "full output is in the transcript]")
    return messages


def write_transcript(messages: list) -> Path:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = TRANSCRIPT_DIR / f"transcript_{stamp}_{uuid.uuid4().hex[:8]}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")
    return path


def summarize_history(messages: list) -> str:
    if not messages:
        raise ValueError("Cannot summarize an empty history")
    conversation = json.dumps(messages, default=str, ensure_ascii=False)
    prompt = (
        "Create a compact checkpoint for this coding-agent conversation. "
        "Use these headings: Current goal, User constraints, Confirmed facts, "
        "Changed files, Verification performed, Remaining work, Blockers. "
        "Preserve exact paths, commands, errors, decisions, and unfinished work.\n\n"
        + conversation)
    response = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=SUMMARY_MAX_TOKENS,
        extra_body={"reasoning": {"effort": "none"}})
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Summary was truncated at max_tokens")
    summary = extract_text(response.content)
    if not summary:
        raise RuntimeError("Summary response was empty")
    return summary


def recent_tail_start(messages: list,
                      keep_messages: int = SUMMARY_KEEP_MESSAGES) -> int:
    """Choose a recent tail without separating tool_use from tool_result."""
    tail_start = max(1, len(messages) - keep_messages)
    if (tail_start > 0 and tail_start < len(messages)
            and is_tool_result_message(messages[tail_start])
            and message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    return tail_start


def compact_history(messages: list,
                    keep_messages: int = SUMMARY_KEEP_MESSAGES) -> list:
    if len(messages) <= 1:
        return messages
    transcript = write_transcript(messages)
    print(f"  \033[36m[compact] transcript saved: {transcript}\033[0m")
    tail_start = recent_tail_start(messages, keep_messages)
    older, recent = messages[:tail_start], messages[tail_start:]
    try:
        summary = summarize_history(older)
    except Exception as exc:
        print(f"  \033[31m[compact] summary failed; history kept: {exc}\033[0m")
        return messages
    return [{"role": "user", "content": f"[Compacted checkpoint]\n\n{summary}"},
            *recent]


def reactive_compact(messages: list) -> list:
    return compact_history(messages, keep_messages=5)


def prepare_context(messages: list, system: str = "", tools: list | None = None,
                    reserved_output: int = 0) -> list:
    # Every LLM turn enters through the same context budget pipeline.
    messages[:] = tool_result_budget(messages)
    messages[:] = micro_compact(messages)
    input_tokens = estimate_request_tokens(messages, system, tools or [])
    hard_input_limit = (MODEL_CONTEXT_TOKENS - CONTEXT_SAFETY_MARGIN
                        - max(reserved_output, MIN_OUTPUT_TOKENS))
    if (input_tokens > CONTEXT_COMPACT_THRESHOLD
            or input_tokens > hard_input_limit):
        messages[:] = compact_history(messages)
    return messages
