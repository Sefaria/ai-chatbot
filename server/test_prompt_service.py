#!/usr/bin/env python
"""Test script to verify prompt service loads core prompt correctly."""

import os
import sys

# Add the current directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set up Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbot_server.settings")
import django

django.setup()

from chat.V2.prompts import get_prompt_service


def test_prompt_service():
    """Test that prompt service loads core prompt with tool instructions."""
    print("=== Testing Prompt Service Integration ===\n")

    service = get_prompt_service()

    core_prompt = service.get_core_prompt()

    print(f"Core prompt ID: {core_prompt.prompt_id}")
    print(f"Core prompt length: {len(core_prompt.text)} chars\n")

    # Check for tool usage instructions in system prompt
    checks = {
        "TOOL USAGE (CRITICAL)": "TOOL USAGE (CRITICAL)" in core_prompt.text,
        "MUST use tools": "MUST use the provided Sefaria tools" in core_prompt.text,
        "NEVER answer from memory": "NEVER answer questions about Jewish texts" in core_prompt.text,
    }

    print("\nTool usage checks in system prompt:")
    all_passed = True
    for check_name, result in checks.items():
        status = "✓" if result else "✗"
        print(f"  {status} {check_name}: {result}")
        if not result:
            all_passed = False

    print("\n" + "=" * 60)
    print("First 600 characters of core prompt:")
    print("=" * 60)
    print(core_prompt.text[:600])

    if all_passed:
        print(
            "\n✓ All checks passed! Prompt service correctly loads core prompt with tool instructions."
        )
        return True
    else:
        print("\n✗ Some checks failed. Tool instructions may not be in system prompt.")
        return False


if __name__ == "__main__":
    success = test_prompt_service()
    sys.exit(0 if success else 1)
