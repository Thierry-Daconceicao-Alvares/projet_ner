"""Garde-fou qualité : la CI échoue si le modèle passe sous le seuil de F1.

Le modèle est évalué sur data/dev.spacy (jeu de validation jamais vu à l'entraînement).
"""
import os
from pathlib import Path

import spacy
from spacy.tokens import DocBin
from spacy.training import Example

MODEL = Path(os.getenv("MODEL_PATH", "output/model-best"))
DEV = Path("data/dev.spacy")
F1_MIN = float(os.getenv("F1_MIN", "0.70"))


def test_model_f1_above_threshold():
    nlp = spacy.load(MODEL)
    docs = list(DocBin().from_disk(DEV).get_docs(nlp.vocab))
    examples = [Example(nlp(d.text), d) for d in docs]
    scores = nlp.evaluate(examples)
    f1 = scores["ents_f"]
    print(f"F1 global = {f1:.3f} (seuil {F1_MIN})")
    for lab, s in scores["ents_per_type"].items():
        if lab in ("WEAPON", "MIL_ORG", "MIL_UNIT"):
            print(f"  {lab:<9} P={s['p']:.3f} R={s['r']:.3f} F1={s['f']:.3f}")
    assert f1 >= F1_MIN
