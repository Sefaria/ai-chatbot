from rest_framework.test import APIClient

from chat.views import BETA_MARKER


def test_beta_marker_endpoint_returns_deployment_marker():
    response = APIClient().get("/api/beta-marker")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "marker": BETA_MARKER,
        "branch": "beta",
    }
