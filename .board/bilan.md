Issue : codé

# Bilan — vérification du moteur OpenAI (LiveTalk) après l'extraction vers live_audio.py

## 1. Auto-test de live_talk.py : base (55324fe) vs HEAD
Interpréteur : venv de l'app (`PYTHONPATH=<arbre>/src`). Deux constats :
- Avec ma config utilisateur (`[talk] echo_guard = "off"`), base ET HEAD cassent à la même
  assertion : « the held microphone went out as nothing » (le garde est off, donc rien n'est
  retenu : l'auto-test dépend de la config de la machine, pas du code).
- Avec un `HOME` vierge (config par défaut), base ET HEAD passent cette vérif des zéros puis
  cassent au même endroit : `assert _wait_for(lambda: bool(sent))` du test de délégation
  client (HEAD l.1037, base l.1289 — même ligne, offset = les ~260 lignes déplacées).
  Cause, déjà présente sur la base : `_consult` appelle `_settle(expect_new=True)` qui attend avant
  d'appeler `_append`, plus longtemps que le `_wait_for` du test. Pas une régression : non corrigé
  (hors carte), à reprendre à part. Les assertions qui suivent ne tournent donc ni sur la base ni sur HEAD.

## 2. LiveTalk contre la vraie API OpenAI (gpt-live-1, voix marin)
Nouveau banc `tests/bench_live_talk.py` (HOME vierge, phrase FR synthétisée 24 kHz, mic simulé
temps réel, sans vrai micro) :
- plain : ouverture OK ; premier son audible 917 ms après la fin de la phrase ; tour user + tour
  assistant transcrits ; `_quiet()` et `finished_speaking()` vrais ; error None.
- int (2e phrase par-dessus la réponse) : EchoGate ouvert → haut-parleurs vidés ~1,0–1,3 s après le
  début de la 2e phrase ; 2e tour user transcrit, 2e réponse assistant (« 20 h à Tokyo »
  dans un essai, plus court dans l'autre) ; `finished_speaking()` vrai ; error None.
Rien ne casse : aucune correction de code nécessaire.

## Non fait
Essai au vrai micro/casque : à l'opérateur (recette). Auto-test de LiveTalk : cassé à l'identique
sur la base (voir 1).
