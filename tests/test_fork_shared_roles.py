"""Fork 2026-11-07: one AI may hold several roles (1, 2 or 3 distinct AIs)."""
import itertools
import json

import pytest

from core import role_settings as settings
from core.orchestrator import Orchestrator

ROLES = ('head', 'builder', 'reviewer')
ALL = [dict(zip(ROLES, combo)) for combo in itertools.product(settings.DOMAINS, repeat=3)]


@pytest.mark.parametrize('roles', ALL, ids=lambda r: '-'.join(r.values()))
def test_every_combination_is_saved_and_applied(tmp_path, monkeypatch, roles):
    monkeypatch.setattr(settings, 'PATH', tmp_path / 'roles.json')
    assert settings.save(roles) == roles
    assert json.loads(settings.PATH.read_text()) == roles
    assert settings.current(settings.apply({})) == roles


@pytest.mark.parametrize('roles', [
    {'head': 'claude', 'builder': 'claude', 'reviewer': 'claude'},
    {'head': 'claude', 'builder': 'claude', 'reviewer': 'openai'},
    {'head': 'antigravity', 'builder': 'openai', 'reviewer': 'antigravity'},
])
def test_shared_roles_route_agents_and_bookkeeping(tmp_path, monkeypatch, roles):
    monkeypatch.setattr(settings, 'PATH', tmp_path / 'roles.json')
    settings.save(roles)
    orch = Orchestrator(settings.apply({}))
    domains = [settings.DOMAINS[roles[r]] for r in ROLES]
    assert [orch._get_agent_domain(a) for a in (orch.head, orch.builder, orch.reviewer)] == domains
    assigned = orch.pm.resolve_roles(*(roles[r] for r in ROLES), strict=True)
    assert assigned['can_proceed']
    assert [assigned[r + '_provider'] for r in ROLES] == [roles[r] for r in ROLES]
    with orch.pm._get_conn() as conn:
        rows = dict(conn.execute('SELECT provider, current_role FROM provider_health').fetchall())
    for domain in set(domains):
        expected = [r.upper() for r in ROLES if settings.DOMAINS[roles[r]] == domain]
        assert [x.strip() for x in rows[domain].split(',')] == expected


def test_unknown_provider_still_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'PATH', tmp_path / 'roles.json')
    with pytest.raises(ValueError):
        settings.save({'head': 'claude', 'builder': 'gemini', 'reviewer': 'claude'})
    assert not settings.PATH.exists()
