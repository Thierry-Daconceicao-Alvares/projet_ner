"""Rejoue des articles du corpus vers l'API pour simuler un flux en production.

Usage :
    python scripts/replay_traffic.py --api http://localhost:8000 --n 2000 --rate 20
"""
import argparse
import json
import random
import time
import urllib.request
from pathlib import Path


def post(url: str, payload: dict) -> int:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--corpus", type=Path, default=Path("texts_clean.json"))
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--rate", type=float, default=20, help="requêtes par seconde")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()

    records = json.loads(a.corpus.read_text(encoding="utf-8"))
    random.seed(a.seed)
    sample = random.sample(records, min(a.n, len(records)))
    ok = 0
    t0 = time.time()
    for i, rec in enumerate(sample, 1):
        payload = {"id": str(rec.get("id")), "title": rec.get("title"),
                   "text": rec.get("text") or rec.get("clean_text") or ""}
        if not payload["text"]:
            continue
        try:
            ok += post(f"{a.api}/predict", payload) == 200
        except Exception as e:  # noqa: BLE001
            print("erreur", e)
        time.sleep(max(0.0, i / a.rate - (time.time() - t0)))
        if i % 200 == 0:
            print(f"{i}/{len(sample)} articles envoyés")
    print(f"Terminé : {ok} réponses OK en {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
