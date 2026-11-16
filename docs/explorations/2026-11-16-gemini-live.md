# Gemini Live comme second fournisseur du moteur Live — exploration

*16/11/2026 — carte a7f124b8. Aucun code. Suite : carte 98097dba (réécrite) et quatre nouvelles cartes.*

## La question

Peut-on mettre Gemini Live (Google) à côté de `gpt-live-1` dans le moteur Live, compagnon compris, sans
ajouter de branche dans le cœur ? Qu'est-ce qui se transpose de `services/live_talk.py`, et qu'est-ce qui manque ?

**Réponse courte : oui, et c'est plus simple que gpt-live-1.** Gemini donne ce que l'API d'OpenAI ne donne pas
(fin de tour, interruption, reprise de session). Il lui manque une seule chose : la délégation vers notre modèle,
qu'on reconstruit avec un outil asynchrone. Essai réel : **≈ 1,1 s** entre la fin d'une phrase française et le
premier son.

## L'API aujourd'hui

Sources : documentation officielle Google lue via ctx7 (`/websites/ai_google_dev_gemini-api`) le 16/11/2026 —
`ai.google.dev/gemini-api/docs/` : `models`, `live-api/capabilities`, `live-api/tools`, `live-api/session-management`,
`live-api/best-practices`, `live-api/live-transcribe`, `live-api/thinking`, `models/gemini-3.8-live`, `pricing`,
`generate-content/speech-generation`, `generate-content/media-resolution`. Liste des modèles vérifiée sur `GET /v1beta/models`
avec notre clé le même jour.

| Sujet | Ce que dit la doc |
|---|---|
| Modèles | `gemini-3.8-live` (défaut recommandé, faible latence), `gemini-3.8-live-extended-thinking` (raisonnement de fond, `thinking_level` **obligatoire** : low/medium/high), `gemini-3.1-flash-live-preview` (ancien), `gemini-2.5-flash-native-audio-*` (ancien). Tous visibles avec notre clé. |
| Transport | WebSocket `BidiGenerateContent`, **v1beta**. SDK Python `google-genai` (3.3.0 testé). |
| Audio | PCM 16 bits little-endian. Entrée 16 kHz native (autre débit rééchantillonné si `audio/pcm;rate=N`), sortie **24 kHz**. Morceaux de 20–40 ms recommandés. |
| Voix, langue | Les 30 voix préconstruites des TTS (Kore, Puck, Charon…), via `speech_config`. La langue est **choisie automatiquement**, on ne peut pas la forcer. Le français marche (essai). |
| Tours | VAD serveur par défaut ; `turn_complete`, `generation_complete` ; transcriptions entrée/sortie sur demande. VAD manuel (`activity_start/end`) ou hybride (`audio_stream_end` envoyé par le client pour finaliser plus vite). |
| Interruption | Le serveur la détecte et envoie `interrupted: true` ; au client de vider sa file de lecture. |
| Outils | Function calling pendant la session. Sur 3.8 : **asynchrone par défaut** (`NON_BLOCKING`), la réponse porte un `scheduling` : `INTERRUPT`, `WHEN_IDLE` ou `SILENT`. `BLOCKING` reste possible (pas sur Extended Thinking). Outil natif : `google_search`. |
| Contexte | `system_instruction` fixée à l'ouverture (la doc ne propose pas de la modifier en cours). `send_client_content` (rôles `user`/`model`) marche **tout au long de la session** : sans `turn_complete`, le modèle attend la suite au lieu de répondre ; avec, il interrompt toujours la génération en cours. |
| Durée | Connexion ≈ 10 min, précédée d'un `GoAway`. Session audio seule : 15 min, audio+vidéo : **2 min**, sauf compression du contexte (`sliding_window`) → illimitée. **Reprise de session** : jeton `new_handle`, valable 2 h après la coupure. |
| Vidéo/écran | Images JPEG/PNG via `send_realtime_input(video=…)`, **1 image/s au plus**. 70 jetons/image en résolution basse/moyenne, 280 en haute (texte dense). Sur 3.8, chaque image envoyée entre dans le contexte : n'envoyer qu'au besoin. |
| Prix (payant) | Entrée audio **0,005 $/min**, sortie audio **0,018 $/min**, image/vidéo 0,002 $/min, texte 0,75 $ / 4,50 $ le million. « Proactive audio » est **toujours actif** sur 3.8 : l'entrée est facturée tant que la session écoute. Recherche Google : 5 000/mois gratuites puis 14 $/1000. |

Ordre de grandeur : une session compagnon de 10 min où l'assistant parle 2 min ≈ 10 × 0,005 + 2 × 0,018 ≈ **0,09 $**
(hors jetons texte), contre 10 × 0,05 = 0,50 $ + le backend pour gpt-live-1.

## Essai réel (16/11, clé `GEMINI_API_KEY` de l'environnement)

Phrase « Bonjour, peux-tu me dire en une phrase quelle heure il est à Tokyo quand il est midi à Paris ? » (5,4 s,
voix de synthèse OpenAI), envoyée en temps réel par blocs de 40 ms après 4 s de silence, puis des zéros. Mesure :
fin de la phrase → premier bloc audio reçu sur la socket (avant toute mise en tampon de lecture).

| Modèle | Ouverture | Fin de phrase → 1er son | Réponse |
|---|---|---|---|
| `gemini-3.8-live` (4 essais) | 360–415 ms | **1035 / 1115 / 1173 / 1193 ms** | « Quand il est midi à Paris, il est vingt heures à Tokyo. » |
| idem, rien après la phrase + `audio_stream_end` (3) | 363–510 ms | 1044 / 1073 / 1174 ms | idem |
| `gemini-3.8-live-extended-thinking`, low (2) | 382–393 ms | 1294 / 1348 ms | une fois « Bien sûr, je calcule cela pour vous. » puis la suite en fond |
| `gemini-3.1-flash-live-preview` (2) | 254–488 ms | 1191 / 1208 ms | correcte |

Transcription d'entrée exacte, réponse en français, jeton de reprise reçu à chaque fois. Comparable au ~1,0 s de
Realtime noté dans CLAUDE.md. Trois pièges rencontrés, à reporter dans le module :

- **Le SDK 3.3.0 ouvre en `v1` par défaut → HTTP 404.** Il faut `HttpOptions(api_version="v1beta")`.
- Extended Thinking refuse d'ouvrir sans `thinking_level` (erreur 1007).
- Avec une voix robotique (espeak) sans silence avant, le serveur a pris la première syllabe pour un tour, a
  répondu « Bonjour, comment puis-je vous aider ? » par-dessus le reste de la phrase, ne s'est **pas** interrompu
  et n'a jamais répondu à la vraie question. Une voix naturelle précédée de silence n'a jamais reproduit ça ;
  à surveiller avec un vrai micro.

Script d’essai jetable, non versionné.

## Correspondance avec notre moteur Live

| Point | gpt-live-1 (`live_talk.py`) | Gemini Live | Bilan |
|---|---|---|---|
| Qui pense | Délégation `client` (notre modèle via `consult()`) ou `responses` (backend OpenAI) | Gemini lui-même ; **pas de délégation** | **Manque.** Se reconstruit : un outil `NON_BLOCKING` unique (« consulter ») qui lance `TalkSession.consult()` sur un thread et répond en `WHEN_IDLE`. Ou mode « natif » : les plugins déclarés comme fonctions Gemini (comme Realtime). |
| Boucle d'agent, MCP | Sous `client` : `_with_tools`, `find_tools` à la demande | Mode consult : inchangé (c'est notre boucle). Mode natif : liste fixée à l'ouverture comme `responses` (pas de mise à jour des outils documentée) | Consult = tout se transpose, MCP différés compris. |
| Le oui parlé | `TalkSession.user_turns` grandit | Idem, alimenté par la transcription d'entrée + `turn_complete` | Se transpose, plus fiable (vraie fin de tour). |
| Micro continu / zéros | Obligatoire : un micro retenu envoie des zéros | Flux continu recommandé ; zéros et `audio_stream_end` testés, tous deux OK | **Plus simple.** Garder les zéros (même code, même facturation : l'entrée est payée de toute façon). |
| Fin de tour | Aucun événement : `tick()` + écart `TALK_LIVE_TURN_GAP` | `turn_complete`, transcriptions par tour | **Plus simple** : `tick()` devient presque vide. |
| Fin d'audio | Aucun événement : `_last_audio`, `SILENT_PEAK`, `AUDIO_IDLE` | `generation_complete` / `turn_complete` ; le tampon de lecture reste à vider | **Plus simple** : `_quiet()` = tour terminé et tampon vide. Le serveur envoie-t-il du silence entre les réponses comme OpenAI ? Non observé ; à vérifier au micro. |
| Interruption | Pas de `speech_started` : l'`EchoGate` décide et vide les haut-parleurs | `interrupted: true` du serveur | **Plus simple** au casque. Sur haut-parleurs, l'`EchoGate` devant le micro reste nécessaire (comme Realtime) : le serveur entendrait sa propre voix. |
| Lecture | Callback PortAudio + tampon de gigue (`_on_play`, `PREBUFFER`) | Audio 24 kHz par morceaux | Se transpose tel quel (à factoriser, pas recopier). |
| Contexte écran | `session.thinking.append` (instructions gelées, 500 jetons par ajout) | `send_client_content(turn_complete=False)`, rôle `user` | Se transpose ; pas de rôle système en cours de route, donc encadrer le texte (« contexte, pas une question »), comme `QUIET_CONTEXT`. |
| Réponse d'un plugin / d'une source | `push_result` → `commentary.append` | Réponse d'outil `WHEN_IDLE` ; une source (sans appel) → `send_client_content` | Se transpose ; `WHEN_IDLE` fait le « attendre que l'autre ait fini » côté serveur. |
| Reprise #122 | Tours passés en `input` à l'ouverture | `send_client_content` avec rôles `user`/`model` à l'ouverture (usage documenté) | Se transpose. |
| Durée | Session expirée = finie, pas de reprise | GoAway à ~10 min, **reprise native** 2 h, compression illimitée | **Mieux**, mais **à coder** : sans reconnexion transparente, une conversation meurt à ~10 min (et à 2 min dès qu'une image est envoyée sans compression). |
| Écran en direct | Non (description par le modèle de vision) | Images ≤ 1/s | **Nouveau.** Seulement à la demande (S, « regarde ça ») : voir carte dédiée. |
| Voix, langue | `TALK_LIVE_VOICES` ; français par bossa/tempo | 30 voix, langue automatique | Se transpose ; liste de voix propre au fournisseur. |
| Fermeture | `session.close` puis attendre `session.closed` | Fermer la WebSocket (contexte async du SDK) | Plus simple. |
| Micro avant haut-parleurs (BT) | `await_headset_mic` | Idem | Commun à garder. |

## Architecture recommandée

**Un second module, `services/gemini_live.py`, classe `GeminiLive`**, avec la même interface publique que
`LiveTalk` — exactement ce que lit `_talk_converse_server` : `start`, `close`, `tick`, `push_context`,
`push_result`, `finished_speaking`, `idle_for`, `done`, `error`, plus `prefix` et `on_level` pour le compagnon.
Le cœur ne change pas : `_talk_conversation` et `companion._converse` choisissent la classe d'après le fournisseur.

- **Pas un quatrième moteur.** `[talk] engine = "live"` reste ; une clé `[talk] live_provider = "openai" | "google"`
  (et `[companion] live_provider`, vide = celle de la conversation, comme `live_options()`) choisit la classe. C'est la
  demande de la carte 98097dba : un groupe « Live », deux fournisseurs.
- **Pas d'héritage de `LiveTalk`** : son protocole (délégation, `tick` par écarts, `SILENT_PEAK`) est justement ce
  qui ne se transpose pas. Ce qui est commun et déjà indépendant du protocole — `EchoGate`, le tampon de gigue
  `_on_play`, `_mic_chunks` avec ses zéros, `await_headset_mic` — est sorti dans un petit module partagé au premier
  besoin, pas recopié.
- **Plugins dérivés de `Plugin`**, jamais écrits deux fois : `Desks` gagne une forme Gemini (déclaration + `behavior`),
  comme `chat_tools`/`realtime_tools`. `tests/test_plugins.py` pilote le plugin inconnu à travers ce moteur aussi.
- **SDK** : extra optionnel `.[gemini]` (`google-genai`), import paresseux ; absent → repli sur la cascade, dit à voix
  haute, comme les autres moteurs serveur. Le SDK suffit (asyncio, comme `live_talk.py` ; pas de WebSocket à la main).
- **Clé** : `GEMINI_API_KEY` (nom lu par le SDK), gérée dans Réglages › Modèles et clés › Clés comme les autres
  (`secrets.py`, masquée par `diagnostics.redact` — à vérifier pour le format `AIza…`).

Alternative écartée : passer par le mode `responses` de l'abstraction OpenAI ou par une couche « fournisseur »
générique dans `live_talk.py`. Les deux protocoles diffèrent sur les points mêmes que ce module encode
(fin de tour, fin d'audio, délégation) ; une seule classe aux deux comportements serait des `if google:` partout.

## Questions pour toi

1. **Qui pense sous Google par défaut ?** « consult » (notre modèle, MCP différés, mêmes réponses qu'avec OpenAI,
   un peu plus lent) ou « natif » (Gemini appelle les plugins lui-même, plus rapide, liste d'outils fixée). Je
   recommande `consult` par défaut et `natif` en option, comme `client`/`responses` aujourd'hui.
2. **Quel modèle par défaut ?** `gemini-3.8-live` (1,1 s) ou Extended Thinking (1,3 s, réfléchit en fond mais
   annonce « je calcule » avant de répondre). Je recommande 3.8-live.
3. **Un fournisseur pour tout, ou le compagnon à part ?** Le compagnon a déjà ses propres champs Live ; le plus
   cohérent est un `live_provider` à lui aussi, vide = celui de la conversation.
4. Free tier : la doc dit que les données y servent à améliorer les produits Google. Accepté pour une
   conversation vocale avec écran, ou facturation obligatoire ?

## Découpage (cartes créées)

1. **98097dba (réécrite)** — Moteur minimal : F4/F6 parlent à Gemini Live (audio, tours, interruption, écho, repli, clé, choix du fournisseur).
2. **5071c294** — Outils sous Gemini Live : « consult » et natif, oui parlé, plugins/MCP/sources — dépend de 1.
3. **f57e4839** — Sessions longues : GoAway, reprise transparente, compression, reprise #122 — dépend de 1.
4. **e7d6d16a** — Le compagnon sur Gemini Live — dépend de 2 (et de 3).
5. **2f3c461b** — L'écran à la demande (images à 1/s au lieu de la description) — dépend de 3.

gpt-live-1.1 (carte d458a07c) : toujours absent de l'API au 16/11 (`/v1/models` ne liste que `gpt-live-1`).
