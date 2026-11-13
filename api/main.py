"""
API d'inférence NER — OSINT TASS
================================
Expose le modèle spaCy fine-tuné (WEAPON / MIL_ORG / MIL_UNIT) en service web.

Routes
  GET  /health          état du service, du modèle et de la connexion Elasticsearch
  GET  /model/info      métriques du modèle servi (précision, rappel, F1), lues dans meta.json
  POST /predict         entités d'un article
  POST /predict/batch   entités de 1 à 100 articles
  POST /index           analyse l'article puis l'indexe dans Elasticsearch (si ES_URL est défini)
  GET  /metrics         métriques lues par Prometheus
  GET  /docs            documentation Swagger (fichiers servis en local)

Lancement local :
  uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from pathlib import Path

import spacy
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, Histogram, generate_latest
from prometheus_client import Counter as PCounter
from pydantic import BaseModel, Field

LABELS = ("WEAPON", "MIL_ORG", "MIL_UNIT")
MODEL_PATH = Path(os.getenv("MODEL_PATH", "output/model-best"))
ES_URL = os.getenv("ES_URL")                      # ex. http://localhost:9200
ES_INDEX = os.getenv("ES_INDEX", "tass_ner_v2")
MAX_CHARS = int(os.getenv("MAX_CHARS", "100000"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("ner_api")

# ── Monitoring (Prometheus) ──────────────────────────────────────────────
REQUESTS = PCounter("ner_api_requests_total", "Requêtes reçues",
                    ["endpoint", "status"])
LATENCY = Histogram("ner_api_request_latency_seconds", "Temps de réponse",
                    ["endpoint"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5))
ENTITIES = PCounter("ner_entities_total", "Entités détectées", ["label"])
ARTICLES = PCounter("ner_articles_total", "Articles analysés")
EMPTY = PCounter("ner_articles_without_entity_total",
                 "Articles sans aucune entité militaire (signal de dérive)")
TEXT_LEN = Histogram("ner_article_chars", "Longueur des articles (caractères)",
                     buckets=(250, 500, 1000, 2000, 4000, 8000, 16000))
MODEL_F1 = Gauge("ner_model_f1", "F1 du modèle sur le jeu de validation",
                 ["label"])

# ── Chargement du modèle ─────────────────────────────────────────────────
log.info("Chargement du modèle %s", MODEL_PATH)
nlp = spacy.load(MODEL_PATH)
META = json.loads((MODEL_PATH / "meta.json").read_text(encoding="utf-8"))
PERF = META.get("performance", {})
MODEL_F1.labels("GLOBAL").set(PERF.get("ents_f", 0.0))
for _lab in LABELS:
    MODEL_F1.labels(_lab).set(PERF.get("ents_per_type", {}).get(_lab, {}).get("f", 0.0))

_es = None
if ES_URL:
    from elasticsearch import Elasticsearch
    _es = Elasticsearch(ES_URL)

app = FastAPI(title="OSINT NER — TASS",
              description="Extraction d'entités militaires (WEAPON, MIL_ORG, MIL_UNIT) "
                          "dans des articles de presse.",
              version=os.getenv("API_VERSION", "1.0.0"),
              docs_url=None)

# Documentation Swagger servie en local (Swagger UI 5 embarqué : aucune dépendance à un CDN)
_STATIC = Path(__file__).parent / "static"
app.mount("/docs-static", StaticFiles(directory=_STATIC), name="docs-static")
_DOCS_JS, _DOCS_CSS = "/docs-static/swagger-ui-bundle.js", "/docs-static/swagger-ui.css"


@app.get("/docs", include_in_schema=False)
def docs():
    return get_swagger_ui_html(openapi_url=app.openapi_url, title="API NER — TASS",
                               swagger_js_url=_DOCS_JS, swagger_css_url=_DOCS_CSS,
                               swagger_favicon_url="")


# ── Schémas ──────────────────────────────────────────────────────────────
class Article(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_CHARS,
                      examples=["Russia deployed the S-400 missile system near the NATO border."])
    title: str | None = None
    id: str | None = None
    date: int | None = Field(None, description="Timestamp Unix (secondes)")


class Batch(BaseModel):
    items: list[Article] = Field(..., min_length=1, max_length=100)


# ── Logique ──────────────────────────────────────────────────────────────
def analyse(article: Article) -> dict:
    text = f"{article.title}. {article.text}" if article.title else article.text
    doc = nlp(text)
    ents = [{"text": e.text, "label": e.label_, "start": e.start_char, "end": e.end_char}
            for e in doc.ents if e.label_ in LABELS]
    counts = Counter(e["label"] for e in ents)
    ARTICLES.inc()
    TEXT_LEN.observe(len(text))
    for lab, n in counts.items():
        ENTITIES.labels(lab).inc(n)
    if not ents:
        EMPTY.inc()
    def uniq(lab):
        return sorted({e["text"] for e in ents if e["label"] == lab})
    return {"id": article.id, "entities": ents,
            "counts": {lab: counts.get(lab, 0) for lab in LABELS},
            "weapons": uniq("WEAPON"), "mil_orgs": uniq("MIL_ORG"),
            "mil_units": uniq("MIL_UNIT")}


@app.middleware("http")
async def monitor(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    path = request.url.path
    if path != "/metrics":
        LATENCY.labels(path).observe(time.perf_counter() - start)
        REQUESTS.labels(path, str(response.status_code)).inc()
    return response


# ── Endpoints ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "model": str(MODEL_PATH), "labels": list(LABELS),
            "elasticsearch": bool(_es)}


@app.get("/model/info")
def model_info():
    per = PERF.get("ents_per_type", {})
    return {"spacy_version": META.get("spacy_version"),
            "pipeline": META.get("pipeline"),
            "f1": PERF.get("ents_f"), "precision": PERF.get("ents_p"),
            "recall": PERF.get("ents_r"),
            "per_label": {lab: per.get(lab) for lab in LABELS}}


@app.post("/predict")
def predict(article: Article):
    return analyse(article)


@app.post("/predict/batch")
def predict_batch(batch: Batch):
    return {"results": [analyse(a) for a in batch.items]}


@app.post("/index")
def index(article: Article):
    if _es is None:
        raise HTTPException(503, "Elasticsearch non configuré (variable ES_URL)")
    res = analyse(article)
    doc = {"title": article.title, "text": article.text,
           "date": article.date * 1000 if article.date else None,   # secondes → ms
           "weapons": res["weapons"], "mil_orgs": res["mil_orgs"],
           "mil_units": res["mil_units"],
           "entity_counts": res["counts"],
           "entity_total": sum(res["counts"].values())}
    out = _es.index(index=ES_INDEX, id=article.id, document=doc)
    return {"indexed": out["result"], "index": ES_INDEX, "id": out["_id"],
            "counts": res["counts"]}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
