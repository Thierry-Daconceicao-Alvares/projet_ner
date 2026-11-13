"""Tests de l'API d'inférence (lancés par la CI à chaque push sur main et à chaque pull request)."""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

SAMPLE = ("The Russian Defense Ministry said air defense forces shot down a HIMARS "
          "rocket and destroyed two Kalibr cruise missiles launchers. "
          "NATO continues to supply S-400 style air defense systems.")


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["labels"] == ["WEAPON", "MIL_ORG", "MIL_UNIT"]


def test_model_info_has_metrics():
    r = client.get("/model/info")
    assert r.status_code == 200
    assert 0 < r.json()["f1"] <= 1


def test_predict_returns_military_entities():
    r = client.post("/predict", json={"text": SAMPLE})
    assert r.status_code == 200
    body = r.json()
    labels = {e["label"] for e in body["entities"]}
    assert labels <= {"WEAPON", "MIL_ORG", "MIL_UNIT"}
    assert body["counts"]["WEAPON"] + body["counts"]["MIL_ORG"] >= 1
    for e in body["entities"]:                      # offsets cohérents
        assert SAMPLE[e["start"]:e["end"]] == e["text"]


def test_predict_rejects_empty_text():
    assert client.post("/predict", json={"text": ""}).status_code == 422


def test_batch():
    r = client.post("/predict/batch", json={"items": [{"text": SAMPLE}, {"text": "Hello."}]})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 2


def test_index_without_elasticsearch_returns_503():
    assert client.post("/index", json={"text": SAMPLE}).status_code == 503


def test_metrics_exposed():
    client.post("/predict", json={"text": SAMPLE})
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "ner_api_requests_total" in r.text
    assert "ner_entities_total" in r.text
