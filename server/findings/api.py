"""JSON API for findings, served on the chat host under /api.

- ``GET  /api/findings/ref/<ref>``  findings anchored at a section or segment (the reader sidebar)
- ``GET  /api/findings``            search: ``ref``, ``method``, ``skill``, ``q``, ``limit``
- ``GET  /api/findings/<id>``       one finding
- ``GET  /api/methods``             the methods, their skills and how many findings each has
- ``POST /api/findings``            record a finding (MCP process only, with a write token)

Public reads are cacheable for a minute and open to any origin, so the reader on sefaria.org
can call them directly.
"""

from __future__ import annotations

import json
import uuid

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from . import refs, service, write_tokens
from .models import Finding

PUBLIC_CACHE_SECONDS = 60
LIST_LIMIT_MAX = 100


def _public(data: dict, status: int = 200) -> JsonResponse:
    response = JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})
    if status == 200:
        response["Cache-Control"] = f"public, max-age={PUBLIC_CACHE_SECONDS}"
    return response


def _limit(request: HttpRequest, default: int = 20) -> int:
    try:
        return max(1, min(int(request.GET.get("limit", default)), LIST_LIMIT_MAX))
    except ValueError:
        return default


@require_GET
def findings_for_ref(request: HttpRequest, ref: str) -> JsonResponse:
    ref = refs.from_url(ref)
    all_roles = request.GET.get("roles") == "all"
    qs = (
        service.matching_ref(service.visible(), ref, all_roles=all_roles)
        .prefetch_related(service.sidebar_refs())
        .order_by("-created")[:LIST_LIMIT_MAX]
    )
    return _public({"ref": ref, "findings": [service.to_json(f, ref) for f in qs]})


@csrf_exempt
def findings_collection(request: HttpRequest) -> JsonResponse:
    if request.method == "POST":
        return _create(request)
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    qs = service.search(
        ref=request.GET.get("ref") or None,
        method=request.GET.get("method") or None,
        skill=request.GET.get("skill") or None,
        text=(request.GET.get("q") or "").strip() or None,
    )
    total = qs.count()
    page = qs.prefetch_related(service.sidebar_refs()).order_by("-created")[: _limit(request)]
    ref = refs.from_url(request.GET["ref"]) if request.GET.get("ref") else None
    return _public({"count": total, "findings": [service.to_json(f, ref) for f in page]})


def _create(request: HttpRequest) -> JsonResponse:
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    user_id = write_tokens.user_id_from(
        token, settings.MCP_OAUTH_SIGNING_KEY or "", settings.MCP_OAUTH_ISSUER
    )
    if not user_id:
        return JsonResponse({"error": "A valid findings write token is required"}, status=401)
    try:
        payload = json.loads(request.body or b"null")
    except ValueError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    try:
        if isinstance(payload, dict) and payload.get("dry_run"):
            # Checks a save without reading or touching the sheet, before the tool creates one.
            service.check(user_id, payload, need_sheet=False)
            return JsonResponse({"ok": True})
        finding, created = service.record(user_id, payload)
    except service.FindingError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    finding = Finding.objects.prefetch_related(service.sidebar_refs()).get(pk=finding.pk)
    return JsonResponse(
        {"created": created, "finding": service.to_json(finding)},
        status=201 if created else 200,
        json_dumps_params={"ensure_ascii": False},
    )


@require_GET
def finding_detail(request: HttpRequest, finding_id: uuid.UUID) -> JsonResponse:
    finding = (
        service.visible().prefetch_related(service.sidebar_refs()).filter(pk=finding_id).first()
    )
    if finding is None:
        return JsonResponse({"error": "Not found"}, status=404)
    return _public({"finding": service.to_json(finding)})


@require_GET
def methods(request: HttpRequest) -> JsonResponse:
    catalog = service.catalog()
    counts = service.method_counts()
    skill_counts = service.skill_counts()
    data = []
    for slug, method in catalog.methods.items():
        skills = catalog.skills_of(slug)
        if not skills:
            continue
        data.append(
            {
                **method.to_json(),
                "url": f"{settings.CHAT_PUBLIC_URL}/findings/methods/{slug}/",
                "findingCount": counts.get(slug, 0),
                "skills": [
                    {
                        "name": s.name,
                        "uri": f"skill://{s.name}/SKILL.md",
                        "description": s.description,
                        "version": s.version,
                        "author": catalog.authors[s.author].name,
                        "findingCount": skill_counts.get(s.name, 0),
                    }
                    for s in skills
                ],
            }
        )
    return _public({"methods": data})
