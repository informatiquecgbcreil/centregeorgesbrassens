import re
from pathlib import Path

from app import create_app


def test_healthz_endpoint_returns_ok():
    app = create_app()
    client = app.test_client()

    resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_templates_url_for_targets_existing_endpoints():
    app = create_app()
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    pattern = re.compile(r"\burl_for\(\s*['\"]([^'\"]+)['\"]")

    missing = []
    for template in Path("app/templates").rglob("*.html"):
        content = template.read_text(encoding="utf-8", errors="ignore")
        for endpoint in pattern.findall(content):
            if endpoint not in endpoints:
                missing.append((str(template), endpoint))

    assert not missing, f"Missing endpoints in templates: {sorted(set(missing))}"
