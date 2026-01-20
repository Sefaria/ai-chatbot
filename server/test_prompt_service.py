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

from chat.prompts import get_prompt_service


def test_prompt_service():
    """Test that prompt service loads core prompt with tool instructions."""
    print("=== Testing Prompt Service Integration ===\n")

    service = get_prompt_service()

    # Get prompt bundle for HALACHIC flow (which should include core prompt)
    bundle = service.get_prompt_bundle(flow="HALACHIC")

    print(f"Core prompt ID: {bundle.core_prompt_id}")
    print(f"Core prompt length: {len(bundle.core_prompt)} chars")
    print(f"Flow prompt length: {len(bundle.flow_prompt)} chars")
    print(f"Combined system prompt length: {len(bundle.system_prompt)} chars\n")

    # Check that system prompt includes both core and flow prompts
    has_core = bundle.core_prompt in bundle.system_prompt
    print(f"System prompt includes core prompt: {has_core}")

    # Check for tool usage instructions in system prompt
    checks = {
        "TOOL USAGE (CRITICAL)": "TOOL USAGE (CRITICAL)" in bundle.system_prompt,
        "MUST use tools": "MUST use the provided Sefaria tools" in bundle.system_prompt,
        "NEVER answer from memory": "NEVER answer questions about Jewish texts"
        in bundle.system_prompt,
    }

    print("\nTool usage checks in system prompt:")
    all_passed = True
    for check_name, result in checks.items():
        status = "✓" if result else "✗"
        print(f"  {status} {check_name}: {result}")
        if not result:
            all_passed = False

    print("\n" + "=" * 60)
    print("First 600 characters of system prompt:")
    print("=" * 60)
    print(bundle.system_prompt[:600])

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
