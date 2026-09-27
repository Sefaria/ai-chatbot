"""
Personal memory: what a signed-in user told the "Personalize Responses" onboarding.

    GET    /api/v2/memory?userId=<token>  → {"memory": {...} | null}
    PUT    /api/v2/memory                 → save (body: userId + answers)
    DELETE /api/v2/memory?userId=<token>  → forget

The saved answers are rendered into a prompt section on every turn
(see prompt_fragments.USER_MEMORY_SECTION).
"""

from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..auth import AuthenticationError, authenticate_request
from ..models import UserMemory
from .prompts.prompt_fragments import USER_MEMORY_SECTION

# Option keys the widget sends → how the agent reads them.
EXPERIENCE = {
    "never": "has never learned Jewish texts before",
    "a_bit": "has learned Jewish texts a bit",
    "grew_up": "grew up learning Jewish texts",
    "recent": "recently became interested in Jewish texts",
}
ORIENTATION = {
    "spiritual": "more spiritual than intellectual",
    "intellectual": "more intellectual than spiritual",
    "both": "both spiritual and intellectual",
    "unsure": "not sure whether spiritual or intellectual",
}
HEBREW = {
    "none": "no Hebrew",
    "alphabet": "knows the Hebrew alphabet",
    "some": "knows some Hebrew",
    "strong": "strong Hebrew",
    "fluent": "fluent Hebrew",
}

MEMORY_FIELDS = ("experience", "orientation", "hebrew", "notes")


class UserMemorySerializer(serializers.Serializer):
    experience = serializers.CharField(max_length=100, required=False, allow_blank=True)
    orientation = serializers.CharField(max_length=100, required=False, allow_blank=True)
    hebrew = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=250, required=False, allow_blank=True)


def _describe(value: str, options: dict[str, str]) -> str:
    return options.get(value) or f'"{value}" (in their words)'


def build_memory_prompt_text(memory: UserMemory | None) -> str | None:
    """Render a user's memory as prompt lines, or None when there is nothing to say."""
    if memory is None:
        return None
    lines = []
    if memory.experience:
        lines.append(f"- Background: {_describe(memory.experience, EXPERIENCE)}")
    if memory.orientation:
        lines.append(f"- Orientation: {_describe(memory.orientation, ORIENTATION)}")
    if memory.hebrew:
        lines.append(f"- Hebrew: {_describe(memory.hebrew, HEBREW)}")
    if memory.notes:
        lines.append(f'- In their own words: "{memory.notes}"')
    return USER_MEMORY_SECTION.format(memory_lines="\n".join(lines)) if lines else None


def load_memory_prompt_text(actor) -> str | None:
    if actor.is_anonymous:
        return None
    return build_memory_prompt_text(UserMemory.objects.filter(user_id=actor.user_id).first())


def _serialize(memory: UserMemory) -> dict:
    return {field: getattr(memory, field) for field in MEMORY_FIELDS} | {
        "updatedAt": memory.updated_at.isoformat()
    }


@api_view(["GET", "PUT", "DELETE"])
def user_memory_v2(request):
    body = request.data if request.method == "PUT" else request.query_params
    try:
        actor = authenticate_request(request, {"userId": body.get("userId")})
    except AuthenticationError:
        return Response({"error": "invalid_userId"}, status=status.HTTP_401_UNAUTHORIZED)

    if request.method == "GET":
        memory = UserMemory.objects.filter(user_id=actor.user_id).first()
        return Response({"memory": _serialize(memory) if memory else None})

    if request.method == "DELETE":
        UserMemory.objects.filter(user_id=actor.user_id).delete()
        return Response({"memory": None})

    serializer = UserMemorySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    defaults = {field: serializer.validated_data.get(field, "").strip() for field in MEMORY_FIELDS}
    memory, _ = UserMemory.objects.update_or_create(user_id=actor.user_id, defaults=defaults)
    return Response({"memory": _serialize(memory)})
