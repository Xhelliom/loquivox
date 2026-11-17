"""
Follow-up judge bench: labelled French phrases said right after a session, the
real continuations vs phrases meant for someone else.

    python tests/bench_follow_up.py          # offline: fake judge, self-check of the bench
    python tests/bench_follow_up.py --real   # the configured judge slot (costs ~25 completions)
    python tests/bench_follow_up.py --real --words   # also score the old word list

Exit 1 when the real judge has more than 1 false positive in 20 phrases.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

LAST = ("user: Quel temps fait-il aujourd'hui à Lyon ?\n"
        "assistant: Aujourd'hui, il pleut sur Lyon, avec 12 degrés l'après-midi.")

# (phrase, addressed to the companion?)
CASES = [
    ("Et pour demain ?", True),
    ("Et à Paris, il fera quel temps ?", True),
    ("Il pleut toute la journée ?", True),
    ("Rappelle-moi de prendre un parapluie", True),
    ("Et après-demain ?", True),
    ("Pourquoi il pleut autant en ce moment", True),
    ("Dis-moi aussi pour le vent", True),
    ("Tu peux me le répéter", True),
    ("Merci, et le week-end alors ?", True),
    ("Résume-moi ça en une phrase", True),
    ("Et du coup on mange quoi ?", False),
    ("Je vais déjeuner avec Paul à midi", False),
    ("Ok merci Paul, on se voit plus tard", False),
    ("Tu as vu mon téléphone, chérie ?", False),
    ("Est-ce que tu veux du café ?", False),
    ("Quoi de neuf à la maison ce soir", False),
    ("Je ne te parlais pas", False),
    ("Comment ça va au bureau, Julie ?", False),
    ("Passe-moi le sel s'il te plaît", False),
    ("Oui oui j'arrive, deux minutes", False),
]


def fake(text: str, last: str) -> tuple:
    return text.startswith(("Et pour demain", "Et après-demain")), "fake"


def score(judge) -> dict:
    tp = fp = fn = tn = 0
    wrong = []
    for text, want in CASES:
        got, why = judge(text, LAST)
        tp += got and want
        fp += got and not want
        fn += (not got) and want
        tn += (not got) and not want
        if got != want:
            wrong.append((text, got, why))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": len(CASES),
            "accuracy": (tp + tn) / len(CASES), "wrong": wrong}


def main() -> int:
    assert sum(w for _, w in CASES) == 10 and len(CASES) == 20
    if "--real" not in sys.argv:
        r = score(fake)
        assert r["tp"] == 2 and r["fp"] == 0, r        # the bench counts what it should
        print("✓ bench self-check (fake judge)")
        return 0
    from loquivox import secrets
    secrets.load_secrets()      # the keys managed in Settings
    from loquivox.handlers import companion
    from loquivox.services.ai import AIService
    print("judge slot:", AIService.judge_target())
    judges = [("modèle", companion._ask_follow_up)]
    if "--words" in sys.argv:
        judges.append(("liste de mots", companion.follow_up_verdict))
    code = 0
    for name, judge in judges:
        r = score(judge)
        print(f"{name}: précision {r['accuracy']:.0%} · vrais positifs {r['tp']}/10 · "
              f"faux positifs {r['fp']}/10 · faux négatifs {r['fn']}/10")
        for text, got, why in r["wrong"]:
            print(f"   ✗ {'oui' if got else 'non'} ({why}) ← {text}")
        if name == "modèle" and r["fp"] > 1:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
