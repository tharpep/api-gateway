from app.main import app


def test_vault_routes_are_mounted():
    """Both vault paths are registered on the app built from an empty configuration."""
    paths = {route.path for route in app.routes}
    assert "/vault/capture" in paths
    assert "/vault/notes" in paths
