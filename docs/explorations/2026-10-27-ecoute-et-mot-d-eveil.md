# Écoute permanente et mot d'éveil — exploration du 27/10/2026

Deux questions : **(1)** peut-on se passer du mot d'éveil en devinant qu'une phrase
s'adresse au compagnon ? **(2)** comment remplacer `hey_jarvis` par un mot à nous
(« Hé Loquivox ») ? La seconde est la question ouverte **6a2029b0** de la roadmap
(phase 2 : « accepter un modèle .onnx entraîné ? français incertain »).

## En deux minutes

| | Recommandation | Latence | CPU (Core Ultra 7 265H) | Coût | Faux positifs visés |
|---|---|---|---|---|---|
| **Q1** | Pas d'écoute « sans nom » en continu. D'abord une **fenêtre de relance locale** de 10 s après chaque session, puis, en option, **le nom n'importe où dans la phrase** par transcription locale. | +0,7 s après la fin de la phrase | 0,68 s CPU par seconde de parole entendue (≈ 8 % d'un cœur en moyenne sur une journée à 1 h de parole) ; 0 dans le silence | 0 € (tout local) | ≤ 1 par jour dans la fenêtre ; ≤ 0,5/h pour le nom |
| **Q2** | **Entraîner un modèle openWakeWord « Hé Loquivox »** (voix TTS françaises), Loquivox l'accepte déjà par chemin. | ≈ 0,1–0,2 s après le mot | 1,4 ms par trame de 80 ms = 1,8 % d'un cœur, comme aujourd'hui | 0 à 10 € (Colab, voix TTS cloud), ½ journée humaine | ≤ 0,5/h (cible des modèles openWakeWord), ≤ 10 % de ratés |

Trois choix reviennent au propriétaire (fin du document).

## Ce qui existe déjà

- `services/presence.py` garde le micro **local** ouvert : VAD énergie + openWakeWord
  (`hey_jarvis`), rien ne sort avant `on_wake`. Mesuré ici : **1,4 ms CPU / trame de
  80 ms** (1,8 % d'un cœur) ; le process entier tourne à 2,6 % CPU au repos.
- Une session reste ouverte **12 s** de silence (`COMPANION_IDLE_TIMEOUT`) : c'est déjà
  l'équivalent du *Follow-Up Mode* d'Alexa ou du *Continued Conversation* de Google —
  mais dans le cloud, à 0,05 $/min de voix Live.
- Les phrases d'arrêt (« je ne te parlais pas ») et le journal `faux déclenchement`
  (`log_companion_cut`) : sur le journal réel du 12/09 au 27/10, **179 sessions, 2 faux
  déclenchements signalés** (tous deux coupés à la touche). Le compteur ne voit que les
  sessions coupées sans un mot ; c'est la seule mesure dont on dispose.
- Smart Turn v3 (fin de tour sémantique) est chargé et prêt (`turn_detector.py`).
- whisper.cpp tourne déjà en local (`pywhispercpp`, `ggml-base.bin` installé). Mesuré :
  **4 s de parole → 0,68 s de latence, 2,7 s CPU** (base, 4 threads, français).
- Vérifié : `openwakeword.Model(wakeword_models=["/chemin/modele.onnx"])` charge un
  fichier ; `load_wake_model()` transmet la valeur telle quelle, donc **un chemin dans
  `[companion] wake_word` marche déjà**. Seul Réglages ne propose que les modèles intégrés.

## Q1 — Deviner qu'on lui parle

### Ce que font les autres

- **Alexa, Follow-Up Mode** : après une réponse, l'appareil écoute encore quelques
  secondes sans mot d'éveil ; un réseau combine l'acoustique (LSTM sur bancs de filtres),
  le texte reconnu et la *confiance* du décodeur ASR. Erreur : ~11 % acoustique seule,
  ~20 % texte seul, **~5 % combinés** ([Amazon Science, 2018](https://www.amazon.science/blog/alexa-do-i-need-to-use-your-wake-word-how-about-now) ;
  [Mallidi et al., arXiv:1808.02504](https://arxiv.org/pdf/1808.02504.pdf)). Les traits
  sémantiques gagnent encore 14 % relatifs d'EER ([Gillespie et al., ICASSP 2020, arXiv:2010.01949](https://export.arxiv.org/abs/2010.01949)).
  Amazon ajoute depuis la caméra pour l'orientation du corps ([blog](https://www.amazon.science/blog/how-alexa-knows-when-youre-talking-to-her)).
- **Google, Continued Conversation** : ~**8 s** d'écoute après la réponse, voyant allumé
  ([aide Google](https://support.google.com/googlehome/answer/7685981)). **Look and Talk**
  (Nest Hub Max) : plus de 100 signaux caméra + micro (regard, proximité, lèvres, Face/Voice
  Match), transcription et intention **sur l'appareil** ([Google Research, 2022](https://blog.research.google/2022/07/look-and-talk-natural-conversations.html)).
- **Gemini Live** : pas de détection de destinataire ; une fois lancé c'est un « appel »
  ouvert qu'on interrompt en parlant ; sur Google Home, ~**15 s** de relance après une
  réponse ([Android Authority](https://www.androidauthority.com/gemini-for-home-continued-conversations-3625555),
  [Thurrott](https://www.thurrott.com/?p=335169)).

À retenir : **personne ne fait d'écoute sans nom en continu**. Tous bornent la détection
à une fenêtre courte après un échange, ou exigent un signal fort (regard + visage). La
caméra est exclue ici.

### Les signaux possibles, et ce qu'ils valent pour nous

| Signal | Disponible | Valeur |
|---|---|---|
| Fenêtre de temps après une session | oui | **Le meilleur prédicteur**, gratuit |
| Le nom prononcé n'importe où (« rappelle-le-moi, Loquivox ») | via transcription locale | Fort, rare en faux positif |
| Contexte : une autre appli utilise le micro (visio), musique, plusieurs voix | PipeWire / `media.py` | Fort négatif — à exploiter |
| Texte : 2ᵉ personne, impératif, question, continuité avec le dernier sujet | via transcription locale | Moyen (~20 % d'erreur seul chez Amazon) |
| Prosodie / acoustique (volume, direction de la voix) | micro unique, pas de corpus | Faible sans données d'entraînement |
| Modèle audio de bout en bout | rien de prêt, ni en français | Hors de portée |

### Coût de la transcription continue

- **Locale (whisper.cpp base)** : ne transcrit que ce que la VAD retient. 0,68 s CPU par
  seconde de parole ; 1 h de parole entendue par jour ≈ **41 min CPU ≈ 8,5 % d'un cœur**
  en moyenne sur 8 h (< 1 % de la machine, 16 cœurs). Zéro dans le silence. Le français de
  `base` est approximatif et il écorche un nom inventé : il faut biaiser par `initial_prompt`
  et comparer phonétiquement. Whisper invente du texte sur le bruit : garder la VAD et
  Smart Turn en amont ([note](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison)).
- **Cloud (Groq)** : ~0,04 $/h d'audio, donc quelques centimes par jour — mais **tout ce qui
  est dit dans la pièce partirait**, collègues et appels compris. Exclu : la promesse
  « rien ne part tant qu'on ne lui parle pas » est la condition du mode compagnon.

### Faux positifs : l'ordre de grandeur qui tranche

Même Amazon, avec trois familles de signaux et des millions d'exemples, reste vers **5 %
d'erreur par énoncé**. Dans un bureau ou en visio, le micro entend facilement 50 énoncés
par heure qui ne lui sont pas adressés : **2 à 3 réveils intempestifs par heure**, chacun
ouvrant une session cloud qui parle. Inacceptable hors fenêtre. Dans une fenêtre de 10 s
après une session (0 à 2 énoncés), le même taux donne **moins d'un faux positif par jour**.

### Proposition par étapes

1. **Fenêtre de relance locale** (`services/presence.py`). Quand une session se ferme,
   Presence reprend le micro et ouvre une fenêtre de N s (défaut 10). Un énoncé retenu par
   la VAD et clos par Smart Turn est transcrit **en local** ; une règle simple décide
   (pas une phrase d'arrêt, pas d'autre appli sur le micro, au moins 2 mots, 2ᵉ personne /
   question / impératif, ou mot du dernier échange). Oui → `wake(cause="follow_up")` avec
   l'audio en tête (`take_tail`). Le journal note chaque décision **sans le contenu**.
   Bonus mesurable : on pourra **raccourcir les 12 s de cloud** (0,05 $/min) puisque la
   relance devient locale — au prix de ~1 s de réouverture Live.
2. **Le nom n'importe où dans la phrase** (option, défaut off). Le même chemin, mais en
   permanence : tout énoncé VAD est transcrit en local et réveille s'il contient le nom
   (comparaison phonétique tolérante). Couvre « …, Loquivox » en fin de phrase, ce qu'un
   mot d'éveil ne sait pas faire. Coût chiffré plus haut ; rien n'est gardé.
3. **Détection sans nom hors fenêtre** : **non recommandée**. Seulement si les journaux des
   étapes 1-2 montrent un besoin, avec un petit classifieur local entraîné sur nos propres
   énoncés étiquetés. Pas de carte.

## Q2 — Un autre mot d'éveil

### Options

| Option | Français | Effort | Coût | Verdict |
|---|---|---|---|---|
| **openWakeWord entraîné** | Non garanti : modèles fournis en anglais seulement ; dépend des voix TTS françaises | Notebook Colab, ~75-90 min GPU selon un dépôt communautaire ; dépendances de 2022 (PyTorch 1.13, TF 2.8) | 0 € (Colab gratuit) à ~10 € | **Recommandé** : même moteur, même coût CPU, zéro code d'inférence |
| microWakeWord | Même pipeline (Piper + négatifs openWakeWord) | Pensé pour ESP32 (TFLite micro) | 0 € | Aucun gain sur un PC |
| Porcupine (Picovoice) | Oui | Console web | **Plus de palier gratuit depuis le 30/06/2026**, vente sur devis | Exclu |
| sherpa-onnx KWS (mots-clés sans entraînement) | Aucun modèle KWS français trouvé | — | 0 € | Exclu pour l'instant |
| Mot-clé par transcription locale | Oui, tout mot, toute position | = Q1 étape 2 | 8 % d'un cœur, +0,7 s | Complément, pas remplaçant : trop lent comme seul déclencheur |

Sources : [openWakeWord (PyPI)](https://pypi.org/project/openwakeword),
[openwakeword-colab-2026](https://github.com/alfiedennen/openwakeword-colab-2026),
[hawake-wakeword](https://github.com/IT-BAER/hawake-wakeword) (Piper, Python 3.11),
[DEV : wake word en 2026](https://dev.to/buguatec/how-to-get-a-wake-word-model-in-2026-3amo),
[microWakeWord / ESPHome](https://esphome.io/components/micro_wake_word/),
[fin du palier gratuit Picovoice](https://community.home-assistant.io/t/fyi-picovoice-confirmed-free-tier-accesskeys-will-stop-working-after-june-30-2026/1012744),
[sherpa-onnx](https://pkg.go.dev/github.com/k2-fsa/sherpa-onnx).

### Entraîner « Hé Loquivox » : le plan chiffré

- **Phrase** : 3-4 syllabes, sons rares en français. « Hé Loquivox » (4) plutôt que
  « Hé Loqui » (3, plus proche de mots courants) ou « Loquivox » seul.
- **Positifs** : ~3 000 clips synthétiques. Les voix Piper françaises sont peu nombreuses
  (siwis, upmc, tom, gilles, et MLS multi-locuteurs) → compléter par des voix cloud
  (OpenAI, ElevenLabs) : ~45 000 caractères, **< 1 $**. Plus 50 enregistrements réels de
  l'opérateur pour la validation, jamais pour l'entraînement.
- **Négatifs** : les traits pré-calculés d'openWakeWord (ACAV100M) + phrases françaises
  proches (« et l'eau qui vole », « loquace »…) générées par le même TTS.
- **Machine** : pas de GPU CUDA ici (Core Ultra 7 265H, iGPU Arc) → Colab. 1-2 h GPU,
  ½ journée humaine, surtout pour les dépendances.
- **Critère de réussite**, mesuré sur 8 h d'audio de bureau réel enregistré localement :
  **≤ 0,5 faux réveil / heure** et **≤ 10 % de ratés** sur les 50 prises réelles. Sinon,
  on garde `hey_jarvis` et l'étape 2 de Q1 porte le nom.
- **Côté Loquivox** : un chemin marche déjà dans `config.toml` ; il manque un sélecteur
  de fichier dans Réglages › Compagnon › Général et un ✓/❌ via `wake_problem()`.

## Recommandation

1. **Q2 d'abord, c'est le moins cher** : accepter un `.onnx` dans Réglages (petit), puis
   entraîner et mesurer « Hé Loquivox ». Ça répond à 6a2029b0 par une mesure plutôt que
   par un pari sur le français.
2. **Q1 ensuite, par la fenêtre de relance** : le seul endroit où la détection de
   destinataire est fiable, et qui peut réduire le temps passé dans le cloud.
3. **Le nom n'importe où** en option, une fois la fenêtre en place (même chemin de
   transcription locale).
4. **Pas d'écoute sans nom en continu** : ~2-3 faux réveils/h attendus, même au niveau
   d'Amazon.

## Questions pour le propriétaire

- **Quelle phrase ?** « Hé Loquivox », « Loquivox » seul, autre chose ? L'entraînement en dépend.
- **Transcrire en local tout ce que le micro entend** (étape 2), même sans rien garder ni
  envoyer, est-ce acceptable pour toi et pour les gens autour ? Sinon l'étape 2 tombe et
  seule la fenêtre de relance reste.
- **Raccourcir la session cloud** (12 s aujourd'hui) une fois la relance locale en place :
  moins cher, mais ~1 s de plus pour reprendre la conversation. Quelle durée ?

## Découpage en cartes (créées au backlog, phase « Mémoire et contexte »)

1. Mot d'éveil : choisir un modèle `.onnx` personnalisé dans Réglages. — `258eca32`
2. Mot d'éveil : entraîner « Hé Loquivox » (openWakeWord, TTS français) et mesurer. — `92fc320f` (attend le choix de la phrase)
3. Compagnon : fenêtre de relance locale après une session. — `fad864f3`
4. Compagnon : réveil par le nom n'importe où dans la phrase (transcription locale, option). — `0b473598` (après `fad864f3`, si la question 2 reçoit un oui)
