#!/usr/bin/env python
"""Test script to verify core prompt has tool usage instructions."""

import os
import sys

# Add the current directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set up Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbot_server.settings")
import django

django.setup()

from chat.V2.prompts import get_prompt_service


def test_core_prompt():
    """Test that core prompt contains proper tool usage instructions."""
    print("=== Testing Core Prompt ===\n")

    service = get_prompt_service()
    core_prompt = service.get_core_prompt().text

    # Check for critical sections
    checks = {
        "TOOL USAGE (CRITICAL) section": "TOOL USAGE (CRITICAL)" in core_prompt,
        "MUST use instruction": "MUST use the provided Sefaria tools" in core_prompt,
        "NEVER answer from memory": "NEVER answer questions about Jewish texts" in core_prompt,
        "For specific text requests": "For specific text requests: USE get_text" in core_prompt,
        "For finding sources": "For finding sources: USE text_search" in core_prompt,
    }

    print(f"Prompt length: {len(core_prompt)} chars\n")

    print("Checks:")
    all_passed = True
    for check_name, result in checks.items():
        status = "✓" if result else "✗"
        print(f"  {status} {check_name}: {result}")
        if not result:
            all_passed = False

    print("\n" + "=" * 60)
    print("First 800 characters of prompt:")
    print("=" * 60)
    print(core_prompt[:800])

    if "TOOL USAGE" in core_prompt:
        print("\n" + "=" * 60)
        print("TOOL USAGE section:")
        print("=" * 60)
        tool_start = core_prompt.find("TOOL USAGE")
        tool_end = core_prompt.find("\n\n", tool_start + 200)
        if tool_end == -1:
            tool_end = tool_start + 600
        print(core_prompt[tool_start:tool_end])

    if all_passed:
        print("\n✓ All checks passed! Core prompt has proper tool usage instructions.")
    else:
        print("\n✗ Some checks failed. Tool usage instructions may be incomplete.")

    return all_passed


if __name__ == "__main__":
    test_core_prompt()
