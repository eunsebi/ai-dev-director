"""
fork 2026-11-14: HEAD 대화를 요청(프롬프트)별로 묶고 프로젝트별로 거르기.
- chat_messages.project_path: 메시지를 쓸 때 세션의 프로젝트를 함께 저장한다.
- 예전 메시지(프로젝트 없음)는 PROJECT_SELECTED 알림으로 추정한다.
- GET /chat/history: 모든 세션의 기록(읽기 전용) + 프로젝트 목록.
"""
import json
import threading
import urllib.request

import pytest

import dashboard_server
from dashboard_server import (chat_history, chat_messages_after, render_html, _fill_message_projects,
                              DashboardServer, GLOBAL_SESSION_ID)
from core.state_machine import StateMachine
from core.chat_manager import ChatManager


@pytest.fixture
def cm(isolate_provider_db):
    StateMachine(); dashboard_server._ensure_schema()
    m = ChatManager()
    m.create_session(GLOBAL_SESSION_ID, "/w/app-a", "ANTHROPIC")
    return m


def test_add_message_stamps_session_project(cm):
    cm.add_message(GLOBAL_SESSION_ID, "USER", "첫 요청")
    cm.set_project(GLOBAL_SESSION_ID, "/w/app-b")
    cm.add_message(GLOBAL_SESSION_ID, "USER", "두 번째 요청")
    rows = chat_messages_after(0, GLOBAL_SESSION_ID)
    assert [r["project_path"] for r in rows] == ["/w/app-a", "/w/app-b"]


def test_legacy_rows_inferred_from_project_selected():
    rows = [
        {"id": 1, "session_id": "s", "role": "USER", "content": "a", "project_path": None},
        {"id": 2, "session_id": "s", "role": "SYSTEM", "content": "PROJECT_SELECTED: /w/one", "project_path": None},
        {"id": 3, "session_id": "s", "role": "HEAD", "content": "b", "project_path": None},
        {"id": 4, "session_id": "s", "role": "SYSTEM", "content": "PROJECT_SELECTED: /w/two", "project_path": None},
        {"id": 5, "session_id": "s", "role": "USER", "content": "c", "project_path": None},
        {"id": 6, "session_id": "t", "role": "USER", "content": "d", "project_path": None},
    ]
    _fill_message_projects(rows, {"s": "/w/two", "t": "/w/three"})
    assert [r["project_path"] for r in rows] == ["/w/one", "/w/one", "/w/one", "/w/two", "/w/two", "/w/three"]


def test_history_spans_sessions_and_filters_by_project(cm):
    cm.add_message(GLOBAL_SESSION_ID, "USER", "A 요청")
    cm.add_message(GLOBAL_SESSION_ID, "HEAD", "A 답")
    cm.create_session("chat-2", "/w/app-b", "ANTHROPIC")
    cm.add_message("chat-2", "USER", "B 요청")
    h = chat_history()
    assert [m["content"] for m in h["messages"]] == ["A 요청", "A 답", "B 요청"]
    names = {p["path"]: p for p in h["projects"]}
    assert names["/w/app-a"]["prompts"] == 1 and names["/w/app-a"]["messages"] == 2
    assert names["/w/app-b"]["name"] == "app-b"
    only_a = chat_history("/w/app-a")
    assert [m["content"] for m in only_a["messages"]] == ["A 요청", "A 답"]
    assert all(m["session_id"] == GLOBAL_SESSION_ID for m in only_a["messages"])


def test_render_carries_project_attribute_and_grouping_ui(cm):
    cm.add_message(GLOBAL_SESSION_ID, "USER", "요청 <b>")
    html = render_html()
    assert 'data-project="/w/app-a"' in html
    assert "setupTurnTools" in html and "/chat/history" in html


def test_history_endpoint(cm):
    cm.add_message(GLOBAL_SESSION_ID, "USER", "엔드포인트")
    httpd = DashboardServer(("127.0.0.1", 0), dashboard_server.DashboardHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/chat/history?project=/w/app-a", timeout=5) as r:
            j = json.loads(r.read().decode("utf-8"))
    finally:
        httpd.shutdown(); httpd.server_close()
    assert j["project"] == "/w/app-a" and j["messages"][-1]["content"] == "엔드포인트"
