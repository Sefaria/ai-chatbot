from django import template
from django.utils.safestring import mark_safe

from findings import refs, service

register = template.Library()

ROLE_LABELS = {"anchor": "Passage studied", "position": "Position", "support": "Also cited"}


@register.filter
def reader_url(ref: str) -> str:
    return service.reader_url(ref)


@register.filter
def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


@register.filter
def by_role(roles: list[dict], role: str) -> list[dict]:
    return [r for r in roles or [] if r.get("role") == role]


@register.filter
def ordered_roles(roles: list[dict]) -> list[dict]:
    """Anchors first, then positions, then the rest, each in the order cited."""
    return sorted(roles or [], key=lambda r: refs.ROLE_RANK.get(r.get("role"), 9))


@register.filter
def passage_path(ref: str) -> str:
    return refs.to_url(ref)


@register.filter
def get_item(mapping: dict, key: str):
    return mapping.get(key) if mapping else None


@register.filter
def markdown(text: str) -> str:
    """Render Markdown with raw HTML disabled, so skill text cannot inject markup."""
    from markdown_it import MarkdownIt

    return mark_safe(MarkdownIt("commonmark", {"html": False}).render(text or ""))
