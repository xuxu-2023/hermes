"""Tests for the SharePoint Graph file-send helpers (pure parts)."""

from __future__ import annotations

from plugins.platforms.teams.graph_files import (
    FILE_INFO_CARD_TYPE,
    build_file_info_card,
    resolve_sharepoint,
)


def test_build_file_info_card_parses_etag_and_type():
    card = build_file_info_card({
        "eTag": '"{ABC-123-DEF},5"',  # quoted, braces, version suffix
        "webDavUrl": "https://contoso.sharepoint.com/sites/x/Doc.docx",
        "name": "Meeting minutes.docx",
    })
    assert card["contentType"] == FILE_INFO_CARD_TYPE
    assert card["contentUrl"].endswith("Doc.docx")
    assert card["name"] == "Meeting minutes.docx"
    assert card["content"]["uniqueId"] == "ABC-123-DEF"  # stripped to the GUID
    assert card["content"]["fileType"] == "docx"


def test_build_file_info_card_no_extension():
    card = build_file_info_card({"eTag": "GUID", "webDavUrl": "u", "name": "README"})
    assert card["content"]["fileType"] == ""
    assert card["content"]["uniqueId"] == "GUID"


def test_resolve_sharepoint_none_without_config(monkeypatch):
    for k in ("TEAMS_SHAREPOINT_SITE_ID", "TEAMS_CLIENT_ID", "TEAMS_CLIENT_SECRET", "TEAMS_TENANT_ID"):
        monkeypatch.delenv(k, raising=False)
    assert resolve_sharepoint({}) is None


def test_resolve_sharepoint_from_config(monkeypatch):
    for k in ("TEAMS_SHAREPOINT_SITE_ID", "TEAMS_CLIENT_ID", "TEAMS_CLIENT_SECRET", "TEAMS_TENANT_ID"):
        monkeypatch.delenv(k, raising=False)
    cfg = resolve_sharepoint({
        "sharePointSiteId": "host,site-guid,web-guid",
        "client_id": "c", "client_secret": "s", "tenant_id": "t",
    })
    assert cfg == {"site_id": "host,site-guid,web-guid", "client_id": "c", "client_secret": "s", "tenant_id": "t"}
