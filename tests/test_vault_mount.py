from app.main import app


def test_vault_routes_are_mounted():
    """All vault paths are registered on the app built from an empty configuration."""
    paths = {route.path for route in app.routes}
    assert "/vault/capture" in paths
    assert "/vault/notes" in paths
    assert "/vault/note" in paths
    assert "/vault/topics" in paths
    assert "/vault/file" in paths
    assert "/vault/move" in paths
