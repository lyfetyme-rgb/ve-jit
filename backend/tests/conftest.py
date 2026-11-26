"""Shared test fixtures.

The whole API now requires a verified bearer token. Existing domain tests
should not have to care, so by default we override the token dependency with
a verified-equivalent principal. Security tests opt out with
``@pytest.mark.no_auth_override`` to exercise the real verification path.
"""

import pytest

from app.config import settings
from app.main import app
from app.security.principal import ROLE_PERMISSIONS, Principal, get_current_principal

TEST_PRINCIPAL = Principal(
    subject="test-principal",
    role="system_admin",
    permissions=ROLE_PERMISSIONS["system_admin"],
    tenants=frozenset({"foodlion", "acehardware", "lowes"}),
    stores=frozenset({"FL-VA-042", "ACE-NC-001", "LOW-NC-DC1"}),
    auth_mode="local",
)


@pytest.fixture(autouse=True)
def _authenticated_tests(request):
    # Domain tests share one TestClient IP; real rate limits would trip across
    # the suite. Security tests opt in to the real transport defences.
    saved = (
        settings.RATE_LIMIT_PER_MINUTE,
        settings.RATE_LIMIT_BURST,
        settings.CSRF_ENFORCE,
    )
    settings.RATE_LIMIT_PER_MINUTE = 1_000_000
    settings.RATE_LIMIT_BURST = 1_000_000
    settings.CSRF_ENFORCE = False

    if request.node.get_closest_marker("no_auth_override"):
        yield
        settings.RATE_LIMIT_PER_MINUTE, settings.RATE_LIMIT_BURST, settings.CSRF_ENFORCE = saved
        return

    app.dependency_overrides[get_current_principal] = lambda: TEST_PRINCIPAL
    yield
    app.dependency_overrides.pop(get_current_principal, None)
    settings.RATE_LIMIT_PER_MINUTE, settings.RATE_LIMIT_BURST, settings.CSRF_ENFORCE = saved
