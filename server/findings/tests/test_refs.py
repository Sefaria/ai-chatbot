"""Ref parsing for findings: which cited source covers each segment of a sheet."""

import pytest

from findings import refs


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("Genesis 1:1", ("Genesis", "1:1")),
        ("Shulchan Arukh, Orach Chayim 1:1", ("Shulchan Arukh, Orach Chayim", "1:1")),
        ("Berakhot 2a:3-5", ("Berakhot", "2a:3-5")),
        ("Genesis 1:30-2:2", ("Genesis", "1:30-2:2")),
        ("Rashi on Genesis 1:1:1", ("Rashi on Genesis", "1:1:1")),
        ("Genesis", ("Genesis", None)),
        ("Pirkei Avot", ("Pirkei Avot", None)),
    ],
)
def test_split(ref, expected):
    assert refs.split(ref) == expected


@pytest.mark.parametrize(
    "outer, inner",
    [
        ("Genesis 1", "Genesis 1:5"),
        ("Genesis 1:1-5", "Genesis 1:3"),
        ("Genesis 1:3-5", "Genesis 1:5"),
        ("Genesis 1:30-2:2", "Genesis 1:31"),
        ("Genesis 1:30-2:2", "Genesis 2:1"),
        ("Genesis 1:30-2:2", "Genesis 2:2"),
        ("Berakhot 2a:3-2b:1", "Berakhot 2a:9"),
        ("Berakhot 2a-3a", "Berakhot 2b:4"),
        ("Genesis 1:1", "Genesis 1:1"),
        ("Genesis", "Genesis 4:1"),
        ("Rashi on Genesis 1:1", "Rashi on Genesis 1:1:2"),
    ],
)
def test_contains(outer, inner):
    assert refs.contains(outer, inner)


@pytest.mark.parametrize(
    "outer, inner",
    [
        ("Genesis 1:1-5", "Genesis 1:6"),
        ("Genesis 1:30-2:2", "Genesis 2:3"),
        ("Genesis 1:30-2:2", "Genesis 1:29"),
        ("Genesis 1", "Exodus 1:1"),
        ("Genesis 1:1", "Genesis 1"),  # a segment does not contain its section
        ("Berakhot 2a:3", "Berakhot 2b:3"),
        ("Genesis 1:1", "Rashi on Genesis 1:1:1"),
    ],
)
def test_does_not_contain(outer, inner):
    assert not refs.contains(outer, inner)


@pytest.mark.parametrize(
    "ref, section",
    [
        ("Genesis 1:5", "Genesis 1"),
        ("Berakhot 2a:3", "Berakhot 2a"),
        ("Shulchan Arukh, Orach Chayim 1:1", "Shulchan Arukh, Orach Chayim 1"),
        ("Rashi on Genesis 1:1:1", "Rashi on Genesis 1:1"),
        ("Genesis 1", "Genesis 1"),
        ("Genesis 1:3-5", "Genesis 1:3-5"),
        ("Genesis", "Genesis"),
    ],
)
def test_section_of(ref, section):
    assert refs.section_of(ref) == section


@pytest.mark.parametrize(
    "url, ref",
    [
        ("Genesis.1.5", "Genesis 1:5"),
        ("Genesis 1:5", "Genesis 1:5"),
        ("Shulchan_Arukh,_Orach_Chayim.1.1", "Shulchan Arukh, Orach Chayim 1:1"),
        ("Berakhot.2a.3-5", "Berakhot 2a:3-5"),
        ("Genesis.1.30-2.2", "Genesis 1:30-2:2"),
        ("Genesis", "Genesis"),
        ("Berakhot 2a", "Berakhot 2a"),
    ],
)
def test_from_url(url, ref):
    assert refs.from_url(url) == ref


def test_to_url_round_trips():
    for ref in ["Genesis 1:30-2:2", "Shulchan Arukh, Orach Chayim 1:1", "Berakhot 2a:3", "Genesis"]:
        assert refs.from_url(refs.to_url(ref)) == ref


def test_rows_give_each_segment_its_sources_role():
    roles = [
        {"ref": "Leviticus 19:9-10", "role": "anchor"},
        {"ref": "Mishnah Peah 1:1", "role": "support"},
    ]
    rows = refs.rows_for(roles, ["Mishnah Peah 1:1", "Leviticus 19:10", "Leviticus 19:9"])
    assert rows == [
        ("Leviticus 19:9-10", "Leviticus 19:9", "Leviticus 19", "anchor"),
        ("Leviticus 19:9-10", "Leviticus 19:10", "Leviticus 19", "anchor"),
        ("Mishnah Peah 1:1", "Mishnah Peah 1:1", "Mishnah Peah 1", "support"),
    ]


def test_rows_take_the_strongest_role_when_sources_overlap():
    roles = [
        {"ref": "Genesis 1", "role": "support"},
        {"ref": "Genesis 1:2", "role": "position"},
        {"ref": "Genesis 1:1", "role": "anchor"},
    ]
    rows = {
        seg: role
        for _, seg, _, role in refs.rows_for(roles, ["Genesis 1:1", "Genesis 1:2", "Genesis 1:3"])
    }
    assert rows == {"Genesis 1:1": "anchor", "Genesis 1:2": "position", "Genesis 1:3": "support"}


def test_uncited_segments_are_support_and_unmatched_sources_keep_their_role():
    roles = [{"ref": "Some Complex Node, Introduction", "role": "anchor"}]
    rows = refs.rows_for(roles, ["Psalms 16:8"])
    assert ("Psalms 16:8", "Psalms 16:8", "Psalms 16", "support") in rows
    assert (
        "Some Complex Node, Introduction",
        "Some Complex Node, Introduction",
        "Some Complex Node, Introduction",
        "anchor",
    ) in rows


def test_rows_ignore_unknown_roles_and_duplicates():
    rows = refs.rows_for(
        [{"ref": "Genesis 1:1", "role": "bogus"}, {"ref": "", "role": "anchor"}],
        ["Genesis 1:1", "Genesis 1:1"],
    )
    assert rows == [("Genesis 1:1", "Genesis 1:1", "Genesis 1", "support")]


def test_a_source_dropped_from_the_sheet_loses_its_row():
    roles = [
        {"ref": "Genesis 1:1", "role": "anchor"},
        {"ref": "Complex Node, Introduction", "role": "position"},
    ]
    kept = refs.rows_for(
        roles, ["Genesis 1:1"], included=["Genesis 1:1", "Complex Node, Introduction"]
    )
    dropped = refs.rows_for(roles, ["Genesis 1:1"], included=["Genesis 1:1"])
    assert (
        "Complex Node, Introduction",
        "Complex Node, Introduction",
        "Complex Node, Introduction",
        "position",
    ) in kept
    assert dropped == [("Genesis 1:1", "Genesis 1:1", "Genesis 1", "anchor")]
