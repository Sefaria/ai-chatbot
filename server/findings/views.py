"""Pages for browsing methods, skills and the findings learners saved with them."""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from . import refs, service
from .models import FindingRef

PAGE_SIZE = 12


def _page(request: HttpRequest, qs):
    qs = qs.prefetch_related("refs").order_by("-created")
    return Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))


def _context(**extra) -> dict:
    catalog = service.catalog()
    return {
        "catalog": catalog,
        "chat_url": settings.CHAT_PUBLIC_URL,
        "sefaria_url": settings.SEFARIA_WEB_URL,
        **extra,
    }


def _methods(catalog) -> list[dict]:
    counts = service.method_counts()
    skill_counts = service.skill_counts()
    out = []
    for slug, method in catalog.methods.items():
        skills = catalog.skills_of(slug)
        if skills:
            out.append(
                {
                    "method": method,
                    "count": counts.get(slug, 0),
                    "skills": [
                        {
                            "skill": s,
                            "author": catalog.authors[s.author],
                            "count": skill_counts.get(s.name, 0),
                        }
                        for s in skills
                    ],
                }
            )
    return sorted(out, key=lambda m: (-m["count"], m["method"].title_en))


def home(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()
    if query and refs.split(refs.from_url(query))[1] is not None:
        return redirect("findings:passage", ref=refs.to_url(refs.from_url(query)))
    findings = service.search(text=query or None)
    catalog = service.catalog()
    return render(
        request,
        "findings/home.html",
        _context(
            query=query,
            methods=_methods(catalog),
            page=_page(request, findings),
            total=service.visible().count(),
            passages=FindingRef.objects.filter(
                finding__in=service.visible(), role__in=refs.SIDEBAR_ROLES
            )
            .values("section_ref")
            .distinct()
            .count(),
        ),
    )


def method(request: HttpRequest, slug: str) -> HttpResponse:
    catalog = service.catalog()
    m = catalog.methods.get(slug)
    if m is None or not catalog.skills_of(slug):
        raise Http404("No such method")
    book = request.GET.get("book") or None
    findings = service.search(method=slug, book=book)
    books = service.books_of(service.search(method=slug))
    return render(
        request,
        "findings/method.html",
        _context(
            m=m,
            skills=next(x["skills"] for x in _methods(catalog) if x["method"].slug == slug),
            book=book,
            books=books,
            books_max=max((n for _, n in books), default=1),
            page=_page(request, findings),
        ),
    )


def skill(request: HttpRequest, name: str) -> HttpResponse:
    catalog = service.catalog()
    s = catalog.skill(name)
    if s is None:
        raise Http404("No such skill")
    return render(
        request,
        "findings/skill.html",
        _context(
            s=s,
            m=catalog.methods[s.method],
            author=catalog.authors[s.author],
            files=sorted(p for p in s.files if p != "SKILL.md"),
            page=_page(request, service.search(skill=name)),
        ),
    )


def skill_file(request: HttpRequest, name: str, path: str) -> HttpResponse:
    """A supporting file of a skill, so the relative links in SKILL.md resolve."""
    catalog = service.catalog()
    s = catalog.skill(name)
    path = str(PurePosixPath(path))
    if s is None or path not in s.files or path == "SKILL.md":
        raise Http404("No such file")
    return render(
        request,
        "findings/skill_file.html",
        _context(s=s, m=catalog.methods[s.method], path=path, text=s.files[path].decode("utf-8")),
    )


def passage(request: HttpRequest, ref: str) -> HttpResponse:
    ref = refs.from_url(ref)
    anchored = service.matching_ref(service.visible(), ref)
    cited = (
        service.matching_ref(service.visible(), ref, all_roles=True)
        .exclude(id__in=anchored.values("id"))
        .prefetch_related("refs")
        .order_by("-created")[:PAGE_SIZE]
    )
    return render(
        request,
        "findings/passage.html",
        _context(ref=ref, page=_page(request, anchored), cited=cited),
    )


def detail(request: HttpRequest, finding_id: uuid.UUID) -> HttpResponse:
    finding = service.visible().prefetch_related("refs").filter(pk=finding_id).first()
    if finding is None:
        raise Http404("No such finding")
    catalog = service.catalog()
    sections = {r.section_ref for r in finding.refs.all() if r.role in refs.SIDEBAR_ROLES}
    related = (
        service.visible()
        .exclude(pk=finding.pk)
        .filter(
            id__in=FindingRef.objects.filter(
                Q(section_ref__in=sections), role__in=refs.SIDEBAR_ROLES
            ).values("finding_id")
        )
        .prefetch_related("refs")
        .order_by("-created")[:6]
    )
    return render(
        request,
        "findings/detail.html",
        _context(
            f=finding,
            m=catalog.methods.get(finding.method),
            s=catalog.skills.get(finding.skill),
            anchors=service.anchor_refs(finding),
            api_url=reverse("findings_api_detail", args=[finding.pk]),
            sheet_url=f"{settings.SEFARIA_WEB_URL}/sheets/{finding.sheet_id}",
            related=related,
            role_order=list(refs.ROLE_RANK),
        ),
    )
