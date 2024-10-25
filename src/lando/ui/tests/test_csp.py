import pytest

# See bug 1926964.
pytest.skip(allow_module_level=True)


def test_csp_headers_set(client):
    response = client.get("/")
    assert "Content-Security-Policy" in response.headers
    # Ensure we're using the most secure source by default
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
