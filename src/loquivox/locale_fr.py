"""French catalogue: English message -> French. Placeholders and markup must match
(tests/test_i18n.py checks it). Plain dict so there is no .mo to build."""

CATALOG = {
    "Dictate at the cursor":
        "Dicter au curseur",
    "Talk about what's on screen":
        "Parler de ce qu'il y a à l'écran",
    "Talk it through, then get the text":
        "En discuter, puis obtenir le texte",
    "Pin the chat overlay on top":
        "Épingler la bulle de chat au premier plan",
    "Read AI answers aloud":
        "Lire les réponses de l'IA à voix haute",
    "Cancel recording / transcription":
        "Annuler l'enregistrement / la transcription",
    "Pause / resume the recording":
        "Mettre en pause / reprendre l'enregistrement",
    "Stop & pick this dictation's refinement":
        "Arrêter et choisir le niveau de reformulation de cette dictée",
    "Companion: toggle silence mode":
        "Compagnon : activer/désactiver le mode silence",
    "Companion: open / end a session":
        "Compagnon : ouvrir / terminer une session",
    "Off":
        "Désactivé",
    "Correct":
        "Correction",
    "Light":
        "Léger",
    "Medium":
        "Moyen",
    "Strong":
        "Fort",
    "Custom":
        "Personnalisé",
    "Active":
        "Actif",
    "Silent":
        "Silence",
    "cancel":
        "annuler",
    "send selection":
        "envoyer la sélection",
    "end turn":
        "finir le tour",
    "write it":
        "l'écrire",
    "end":
        "terminer",
    "look again":
        "regarder à nouveau",
    "Connecting…":
        "Connexion…",
    "Thinking…":
        "Réflexion…",
    "📋 Selection sent: {preview}":
        "📋 Sélection envoyée : {preview}",
    "Speaking…":
        "Je parle…",
    "Board":
        "Board",
    "Notifications":
        "Notifications",
    "📋 Board notification":
        "📋 Notification du board",
    "Let the board speak, in conversation and on standby":
        "Laisser le board parler, en conversation et en veille",
    "Only say what goes to the phone":
        "Dire seulement ce qui part sur le téléphone",
    "Filter":
        "Filtre",
    "Collie Board notifies when one of its cards stops and needs you. With this on, a conversation in progress is told too: the assistant interrupts itself, says which card wants what, and asks what to do. It only reads the local board ({url}) — nothing is sent, and an alert you have already handled is not repeated out loud.":
        "Collie Board prévient quand l'une de ses cartes s'arrête et a besoin de vous. Avec cette option, une conversation en cours est prévenue aussi : l'assistant s'interrompt, dit quelle carte demande quoi, et demande quoi faire. Il lit seulement le board local ({url}) — rien n'est envoyé, et une alerte que vous avez déjà traitée n'est pas répétée à voix haute.",
    "<small>✓ Board notifications on — takes effect on the next talk session.</small>":
        "<small>✓ Notifications du board activées — effectif à la prochaine session de conversation.</small>",
    "<small>✓ Board notifications on — but the board did not answer — takes effect on the next talk session.</small>":
        "<small>✓ Notifications du board activées — mais le board n'a pas répondu — effectif à la prochaine session de conversation.</small>",
    "<small>✓ Board notifications off — takes effect on the next talk session.</small>":
        "<small>✓ Notifications du board désactivées — effectif à la prochaine session de conversation.</small>",
    "🧠 Thinking…":
        "🧠 Réflexion…",
    "👁️ Window seen":
        "👁️ Fenêtre vue",
    "👁️ Looking…":
        "👁️ Je regarde…",
    "MCP servers":
        "Serveurs MCP",
    "Servers declared under [mcp.servers.&lt;name&gt;] in config.toml give the conversation their tools. A tool that is not marked read-only is confirmed out loud before it runs. Needs the `mcp` extra. Under Live with OpenAI's backend, the backend's tools are fixed when the session opens: every tool is declared, `always` sorting does not apply there.":
        "Les serveurs déclarés sous [mcp.servers.&lt;nom&gt;] dans config.toml donnent leurs outils à la conversation. Un outil qui n'est pas en lecture seule est confirmé à voix haute avant de s'exécuter. Nécessite l'extra `mcp`. Avec Live et le backend d'OpenAI, les outils du backend sont figés à l'ouverture de la session : tous les outils sont déclarés, le tri `always` ne s'y applique pas.",
    "⚡ = acts: confirmed out loud before it runs":
        "⚡ = agit : confirmé à voix haute avant de s'exécuter",
    "up":
        "actif",
    "starting":
        "démarrage",
    "failed":
        "échec",
    "off":
        "désactivé",
    "No server declared.":
        "Aucun serveur déclaré.",
    "What the companion no longer repeats":
        "Ce que le compagnon ne répète plus",
    "Stop telling me about…":
        "Ne plus me parler de…",
    "Add":
        "Ajouter",
    "Rules learned by voice (\"don't tell me that again\") or added here. A notification covered by a rule does not wake the companion. Edit the text, tick Delete or add a line, then Apply. The file: ":
        "Règles apprises à la voix (« ne me redis plus ça ») ou ajoutées ici. Une notification couverte par une règle ne réveille pas le compagnon. Modifiez le texte, cochez Supprimer ou ajoutez une ligne, puis Appliquer. Le fichier : ",
    "<small>✓ Memory saved — effective immediately.</small>":
        "<small>✓ Mémoire enregistrée — effective immédiatement.</small>",
    "🔇 Rule saved…":
        "🔇 Règle retenue…",
    "What the companion remembers":
        "Ce dont le compagnon se souvient",
    "Facts retained by voice (\"remember that…\") or at the end of a session. Edit the text or the project, tick Delete or add a line, then Apply. The file: ":
        "Faits retenus à la voix (« retiens que… ») ou à la fin d'une session. Modifiez le texte ou le projet, cochez Supprimer ou ajoutez une ligne, puis Appliquer. Le fichier : ",
    "Project":
        "Projet",
    "requested":
        "demandé",
    "automatic":
        "auto",
    "Fact":
        "Fait",
    "{n} fact(s) retained":
        "{n} fait(s) retenu(s)",
    "Remember that…":
        "Retiens que…",
    "Add a fact":
        "Ajouter un fait",
    "<small>✓ Facts saved — effective immediately.</small>":
        "<small>✓ Faits enregistrés — effectifs immédiatement.</small>",
    "🧠 Fact retained…":
        "🧠 Fait retenu…",
    "🧠 Recalling…":
        "🧠 Je cherche dans ce que j'ai retenu…",
    "Delete":
        "Supprimer",
    "Rule":
        "Règle",
    "{n} dropped":
        "{n} écartée(s)",
    "Format":
        "Mise en forme",
    "Search":
        "Recherche",
    "The conversational models are quick and lightly read. When one of them needs a fact, it hands the question to this model, which can search the web, and keeps talking; the answer is given when it comes back. OpenAI's Responses API with web search, or a Groq compound model.":
        "Les modèles de conversation sont rapides et peu érudits. Quand l'un d'eux a besoin d'un fait, il confie la question à ce modèle, qui peut chercher sur le web, et continue de parler ; la réponse est donnée dès qu'elle revient. API Responses d'OpenAI avec recherche web, ou un modèle Groq compound.",
    "Research":
        "Recherche",
    "🔎 Result received — the assistant will tell you":
        "🔎 Résultat reçu — l'assistant vous le donne",
    "Let the conversation look things up":
        "Laisser la conversation faire des recherches",
    "<small>✓ Research on — takes effect on the next talk session.</small>":
        "<small>✓ Recherche activée — effective à la prochaine session de conversation.</small>",
    "<small>✓ Research off — takes effect on the next talk session.</small>":
        "<small>✓ Recherche désactivée — effective à la prochaine session de conversation.</small>",
    "🔎 Research under way: {question}":
        "🔎 Recherche en cours : {question}",
    "Rules":
        "Règles",
    "Event → MCP action rules, written under [[rules]] in config.toml (see config.example.toml; `loquivox --rules-check` validates them).":
        "Règles événement → action MCP, écrites sous [[rules]] dans config.toml (voir config.example.toml ; `loquivox --rules-check` les valide).",
    "not run yet":
        "pas encore exécutée",
    "No rule declared.":
        "Aucune règle déclarée.",
    "✓ ok":
        "✓ ok",
    "○ off · ":
        "○ désactivée · ",
    "🔇 The voice could not be read aloud: {error}":
        "🔇 La voix n'a pas pu être lue : {error}",
    "🔇 The speakers come back into the microphone louder than speech itself ({peak:.3f} against a {threshold:.3f} threshold): interrupting a reply cannot work reliably here. Use a headset, or echo cancellation (PipeWire's libpipewire-module-echo-cancel), selected as the microphone.":
        "🔇 Les haut-parleurs reviennent dans le micro plus fort que la voix elle-même ({peak:.3f} contre un seuil de {threshold:.3f}) : interrompre une réponse ne peut pas fonctionner de façon fiable ici. Utilisez un casque, ou l'annulation d'écho (libpipewire-module-echo-cancel de PipeWire), sélectionnée comme microphone.",
    "Writing":
        "Écriture",
    "In chat mode (F4), let the conversation produce a text without ending: ask for it out loud, it is written from everything said so far and pasted at your cursor, and the conversation carries on — so the next sentence can be \"shorter, and drop the last paragraph\". Off, F4 writes nothing and F6 remains the way to get a text.":
        "En mode chat (F4), laisser la conversation produire un texte sans s'arrêter : demandez-le à voix haute, il est écrit à partir de tout ce qui a été dit et collé à votre curseur, puis la conversation continue — la phrase suivante peut donc être « plus court, et supprime le dernier paragraphe ». Désactivé, F4 n'écrit rien et F6 reste le moyen d'obtenir un texte.",
    "📝 Text written":
        "📝 Texte rédigé",
    "Let the conversation write and paste a text":
        "Laisser la conversation écrire et coller un texte",
    "✍️ Writing…":
        "✍️ Rédaction en cours…",
    "<small>✓ Writing on — takes effect on the next talk session.</small>":
        "<small>✓ Écriture activée — effective à la prochaine session de conversation.</small>",
    "<small>✓ Writing off — takes effect on the next talk session.</small>":
        "<small>✓ Écriture désactivée — effective à la prochaine session de conversation.</small>",
    "The session takes the keyboard while it waits on you. Click to leave it yours: you keep typing and your shortcuts keep working in whatever app has the focus, and these buttons become the way to drive the conversation.":
        "La session prend le clavier pendant qu'elle vous attend. Cliquez pour le garder : vous continuez à taper et vos raccourcis fonctionnent dans l'application qui a le focus, et ces boutons deviennent le moyen de piloter la conversation.",
    "🔒 Keyboard held":
        "🔒 Clavier pris",
    "🔓 Keyboard free":
        "🔓 Clavier libre",
    "🗣️ Talk":
        "🗣️ Conversation",
    "Hide":
        "Masquer",
    "Send":
        "Envoyer",
    "Copy Code":
        "Copier le code",
    "{key}: Unpin":
        "{key} : Désépingler",
    "{key}: Pin":
        "{key} : Épingler",
    "{key}: Mute":
        "{key} : Muet",
    "{key}: Voice":
        "{key} : Voix",
    "Settings":
        "Réglages",
    "Type a message…":
        "Écrire un message…",
    "Loquivox — hotkeys":
        "Loquivox — raccourcis",
    "Enter ✓   ·   C ⧉   ·   R ↻   ·   V 🗣️   ·   Esc ✗":
        "Entrée ✓   ·   C ⧉   ·   R ↻   ·   V 🗣️   ·   Échap ✗",
    "Transcribing…":
        "Transcription…",
    "Choose refinement":
        "Choisir la reformulation",
    "0-5 / ↑↓  ·  Enter ✓  ·  Esc ✗":
        "0-5 / ↑↓  ·  Entrée ✓  ·  Échap ✗",
    "Paused ⏸":
        "En pause ⏸",
    "refine":
        "reformuler",
    "resume":
        "reprendre",
    "pause":
        "pause",
    "Keys, addresses and names are removed either way.":
        "Les clés, adresses et noms sont retirés dans tous les cas.",
    "Save for sharing":
        "Enregistrer pour partager",
    "Include what was said (transcripts, screen descriptions)":
        "Inclure ce qui a été dit (transcriptions, descriptions d'écran)",
    "What a bug report needs: version, system, tools, config and the recent log. <b>API keys, your user name, home directory, e-mail and IP addresses are removed.</b> What you said is replaced by its length unless you tick the box. Read it over, then copy it into a GitHub issue or save it to a file.":
        "Ce qu'il faut à un rapport de bug : version, système, outils, configuration et journal récent. <b>Les clés d'API, votre nom d'utilisateur, votre dossier personnel, votre e-mail et vos adresses IP sont retirés.</b> Ce que vous avez dit est remplacé par sa longueur, sauf si vous cochez la case. Relisez-le, puis copiez-le dans une issue GitHub ou enregistrez-le dans un fichier.",
    "Off: a dropped transcript shows as '[… 42 chars]'. On: the sentence itself — useful when the bug is about what was heard.":
        "Décoché : une transcription retirée apparaît sous la forme « [… 42 car.] ». Coché : la phrase elle-même — utile quand le bug concerne ce qui a été entendu.",
    "Collecting… (a few seconds: the journal and the sound card are asked)":
        "Collecte en cours… (quelques secondes : le journal et la carte son sont interrogés)",
    "Collecting…":
        "Collecte en cours…",
    "📋 Copied — paste it into the issue":
        "📋 Copié — collez-le dans l'issue",
    "Open the whole log in your text editor":
        "Ouvrir le journal complet dans votre éditeur de texte",
    "The whole log, with keys, names and addresses removed — the file to attach to a bug report":
        "Le journal complet, sans clés, noms ni adresses — le fichier à joindre à un rapport de bug",
    "(log file disabled via LOQUIVOX_LOG_FILE)":
        "(fichier journal désactivé via LOQUIVOX_LOG_FILE)",
    "📋 Copied (unredacted)":
        "📋 Copié (non expurgé)",
    "Loquivox — Diagnostic report":
        "Loquivox — Rapport de diagnostic",
    "Close":
        "Fermer",
    "Save…":
        "Enregistrer…",
    "Copy":
        "Copier",
    "Loquivox — Log":
        "Loquivox — Journal",
    "Diagnostic report…":
        "Rapport de diagnostic…",
    "Open file":
        "Ouvrir le fichier",
    "Save for sharing…":
        "Enregistrer pour partager…",
    "<tt>{path}</tt> — last {LINES} lines, refreshed live. Nothing on screen is redacted; <b>Save for sharing…</b> writes the whole log with keys, names and addresses removed.":
        "<tt>{path}</tt> — {LINES} dernières lignes, rafraîchies en direct. Rien à l'écran n'est expurgé ; <b>Enregistrer pour partager…</b> écrit le journal complet sans clés, noms ni adresses.",
    "💾 Saved to {target}":
        "💾 Enregistré dans {target}",
    "(no log yet at {path})":
        "(pas encore de journal dans {path})",
    "(unreadable: {e})":
        "(illisible : {e})",
    "{n} lines":
        "{n} lignes",
    "💾 Redacted log saved to {target}":
        "💾 Journal expurgé enregistré dans {target}",
    "Could not build the report: {e}":
        "Impossible de construire le rapport : {e}",
    "{n} lines · with spoken content":
        "{n} lignes · avec le contenu parlé",
    "{n} lines · without spoken content":
        "{n} lignes · sans le contenu parlé",
    "❌ Could not save: {e}":
        "❌ Enregistrement impossible : {e}",
    "(disabled)":
        "(désactivé)",
    "❌ Could not open: {e}":
        "❌ Ouverture impossible : {e}",
    "(the log is empty)":
        "(le journal est vide)",
    "managed install — add this optional backend via your package manager (or reinstall in a venv), not from the app":
        "installation gérée — ajoutez ce backend optionnel via votre gestionnaire de paquets (ou réinstallez dans un venv), pas depuis l'application",
    "End my turns on meaning, not on silence (cascade)":
        "Terminer mes tours sur le sens, pas sur le silence (cascade)",
    "<small>✓ Saved — the wake word and the click apply now, the startup choice at the next launch.</small>":
        "<small>✓ Enregistré — le mot de réveil et le clic s'appliquent maintenant, le choix de démarrage au prochain lancement.</small>",
    "Dictation":
        "Dictée",
    "Cancel":
        "Annuler",
    "Pause / Resume":
        "Pause / reprise",
    "Stop + choose level":
        "Arrêter + choisir le niveau",
    "Companion silence":
        "Silence du compagnon",
    "Groq Orpheus — English only (needs GROQ_API_KEY)":
        "Groq Orpheus — anglais uniquement (nécessite GROQ_API_KEY)",
    "OpenAI — multilingual, speaks French (needs OPENAI_API_KEY)":
        "OpenAI — multilingue, parle français (nécessite OPENAI_API_KEY)",
    "Piper — local, offline, no key (pip install piper-tts)":
        "Piper — local, hors ligne, sans clé (pip install piper-tts)",
    "Groq — cloud, batch":
        "Groq — cloud, par lots",
    "whisper.cpp — local, offline":
        "whisper.cpp — local, hors ligne",
    "Deepgram — cloud, live":
        "Deepgram — cloud, en direct",
    "OpenAI Realtime — cloud, live":
        "OpenAI Realtime — cloud, en direct",
    "Auto — Groq if key, else local":
        "Auto — Groq si clé, sinon local",
    "Autodetect":
        "Détection automatique",
    "English":
        "Anglais",
    "French":
        "Français",
    "German":
        "Allemand",
    "Spanish":
        "Espagnol",
    "Italian":
        "Italien",
    "Portuguese":
        "Portugais",
    "Dutch":
        "Néerlandais",
    "Russian":
        "Russe",
    "Chinese":
        "Chinois",
    "Japanese":
        "Japonais",
    "Korean":
        "Coréen",
    "Arabic":
        "Arabe",
    "Hindi":
        "Hindi",
    "Polish":
        "Polonais",
    "Save and apply every tab, not just this one":
        "Enregistrer et appliquer tous les onglets, pas seulement celui-ci",
    "Silence the companion":
        "Mettre le compagnon en silence",
    "Wake the companion":
        "Réveiller le compagnon",
    "Companion":
        "Compagnon",
    "A small bubble that listens for its wake word on this machine and opens the cloud conversation only when spoken to.":
        "Une petite bulle qui guette son mot de réveil sur cette machine et n'ouvre la conversation cloud que lorsqu'on lui parle.",
    "Wake word":
        "Mot de réveil",
    "0 = never · takes effect at next launch":
        "0 = jamais · effectif au prochain lancement",
    "Idle (min)":
        "Inactivité (min)",
    "Bubble style":
        "Style de la bulle",
    "Music and other sounds":
        "Musique et autres sons",
    "A classic Bluetooth headset pauses every player when its microphone opens, and nothing resumes them. Lowering the volume instead only shows on speakers, a wired headset or a Bluetooth LE Audio headset — on a classic one it behaves like resume.":
        "Un casque Bluetooth classique met en pause tous les lecteurs quand son micro s'ouvre, et rien ne les relance. Baisser le volume à la place ne se remarque que sur des haut-parleurs, un casque filaire ou un casque Bluetooth LE Audio — sur un casque classique, cela revient à reprendre la lecture.",
    "While recording":
        "Pendant l'enregistrement",
    "Lowered volume (%)":
        "Volume réduit (%)",
    "Lowering only happens with <i>Lower every other sound</i> above.":
        "La réduction n'a lieu qu'avec <i>Baisser tous les autres sons</i> ci-dessus.",
    "Installing…":
        "Installation…",
    "<small>Loading the wake word…</small>":
        "<small>Chargement du mot de réveil…</small>",
    "Fast":
        "Rapide",
    "Natural":
        "Naturel",
    "Speech to speech — one OpenAI Realtime session holds the conversation, ~1.0s and it keeps the prosody. Costs more, and the chat model no longer answers in talk mode.":
        "Parole à parole — une session OpenAI Realtime tient toute la conversation, ~1,0 s et elle garde la prosodie. Coûte plus cher, et le modèle de chat ne répond plus en mode conversation.",
    "Private":
        "Privé",
    "Local voice and offline transcription — nothing but the answer itself leaves the machine. Needs piper-tts and the whisper.cpp binary; anything missing falls back to cloud.":
        "Voix locale et transcription hors ligne — seule la réponse elle-même quitte la machine. Nécessite piper-tts et le binaire whisper.cpp ; ce qui manque bascule sur le cloud.",
    "Presets":
        "Préréglages",
    "Conversation engine":
        "Moteur de conversation",
    "How a conversation is held. The cascade chains the transcription, the chat model and the voice — each swappable, local or cloud, on their own tabs. Realtime hands the whole conversation to one model that hears and answers directly: faster and more natural, but the chat model no longer answers. Live splits the difference — an OpenAI voice holds the conversation and delegates the thinking, which can be your own model again. The final text is a plain completion either way.":
        "Comment une conversation est tenue. La cascade enchaîne la transcription, le modèle de chat et la voix — chacun interchangeable, local ou cloud, dans ses propres onglets. Realtime confie toute la conversation à un seul modèle qui entend et répond directement : plus rapide et plus naturel, mais le modèle de chat ne répond plus. Live fait le compromis — une voix OpenAI tient la conversation et délègue la réflexion, qui peut redevenir votre propre modèle. Le texte final est dans tous les cas une simple complétion.",
    "Engine":
        "Moteur",
    "Realtime model":
        "Modèle Realtime",
    "Preview this voice (same voice, spoken by the TTS model)":
        "Écouter cette voix (la même voix, prononcée par le modèle TTS)",
    "Realtime voice":
        "Voix Realtime",
    "Live voice":
        "Voix Live",
    "Delegate thinking to":
        "Déléguer la réflexion à",
    "Only used when the thinking is delegated to OpenAI's backend":
        "Utilisé uniquement quand la réflexion est déléguée au backend d'OpenAI",
    "OpenAI backend model":
        "Modèle du backend OpenAI",
    "Which web search the OpenAI backend uses. Choosing theirs stands down the search plugin so the two never compete — and only that one: every other plugin stays on the table either way. Their search ignores your search provider and model.":
        "Quelle recherche web utilise le backend OpenAI. Choisir la leur met le plugin de recherche en retrait pour que les deux ne se concurrencent jamais — et seulement lui : tous les autres plugins restent disponibles dans les deux cas. Leur recherche ignore votre fournisseur et votre modèle de recherche.",
    "Web search":
        "Recherche web",
    "Speak a line with the selected engine and voice":
        "Prononcer une phrase avec le moteur et la voix sélectionnés",
    "Piper needs no key and no network: its voice downloads once (~60 MB) and is flatter than the cloud's.":
        "Piper n'a besoin ni de clé ni de réseau : sa voix se télécharge une fois (~60 Mo) et elle est plus plate que celle du cloud.",
    "Recording overlay":
        "Bulle d'enregistrement",
    "The bubble that appears while you record. Everything here is previewed live below; the style and the two columns apply to the next recording.":
        "La bulle qui apparaît pendant l'enregistrement. Tout ce qui est ici est prévisualisé en direct ci-dessous ; le style et les deux colonnes s'appliquent au prochain enregistrement.",
    "Pill — waveform capsule":
        "Pilule — capsule de forme d'onde",
    "Classic — icon + bars":
        "Classique — icône + barres",
    "Style":
        "Style",
    "Color scheme":
        "Palette de couleurs",
    "Applies immediately, to the overlay above and to the chat bubble.":
        "S'applique immédiatement, à la bulle ci-dessus et à la bulle de chat.",
    "Chrome that lives outside a recording. Applies immediately.":
        "Les éléments qui vivent en dehors d'un enregistrement. S'applique immédiatement.",
    "Language":
        "Langue",
    "The language of the whole interface. Auto follows the system locale. Applies immediately; reopen Settings to see this window in the new language.":
        "La langue de toute l'interface. Auto suit la langue du système. S'applique immédiatement ; rouvrez les Réglages pour voir cette fenêtre dans la nouvelle langue.",
    "Transcription":
        "Transcription",
    "Backend":
        "Backend",
    "System default":
        "Par défaut du système",
    "Microphone":
        "Microphone",
    "proper nouns, jargon, foreign words…":
        "noms propres, jargon, mots étrangers…",
    "Comma-separated words the engine tends to drop or misspell — proper nouns, technical terms, foreign words. Write them in the dictation language, e.g. “Loquivox, Wayland, pull request, deploy”.\n\nGroq / whisper.cpp: sent as Whisper's prompt. Deepgram: sent as keyterms (nova-3 only). OpenAI Realtime: not supported.":
        "Mots séparés par des virgules que le moteur tend à omettre ou mal orthographier — noms propres, termes techniques, mots étrangers. Écrivez-les dans la langue de la dictée, p. ex. « Loquivox, Wayland, pull request, deploy ».\n\nGroq / whisper.cpp : envoyés comme prompt de Whisper. Deepgram : envoyés comme keyterms (nova-3 uniquement). OpenAI Realtime : non pris en charge.",
    "Vocabulary":
        "Vocabulaire",
    "Recording":
        "Enregistrement",
    "Dictation key":
        "Touche de dictée",
    "🔴 live transcription":
        "🔴 transcription en direct",
    "📝 batch (transcribing… indicator)":
        "📝 par lots (indicateur « transcription… »)",
    "whisper.cpp: ⚪ engine not found (<tt>whisper-cli</tt> missing)":
        "whisper.cpp : ⚪ moteur introuvable (<tt>whisper-cli</tt> manquant)",
    "Refinement":
        "Reformulation",
    "What an LLM does to a dictation before it is typed. Uses the chat model from the Models &amp; keys tab (needs its API key); Off adds no latency.":
        "Ce qu'un LLM fait à une dictée avant qu'elle soit tapée. Utilise le modèle de chat de l'onglet Modèles et clés (nécessite sa clé d'API) ; Désactivé n'ajoute aucune latence.",
    "Lays the result out in plain-text paragraphs and lists. Combines with the refinement level (or works alone when level is Off).":
        "Met le résultat en forme de paragraphes et de listes en texte brut. Se combine avec le niveau de reformulation (ou fonctionne seul quand le niveau est Désactivé).",
    "Used when the refinement level is set to Custom — greyed out otherwise.":
        "Utilisé quand le niveau de reformulation est Personnalisé — grisé sinon.",
    " + format":
        " + mise en forme",
    "Minimal — only asks when something is unclear":
        "Minimal — ne pose de question que si quelque chose n'est pas clair",
    "Normal — asks about what would change the text":
        "Normal — pose des questions sur ce qui changerait le texte",
    "Deep — pushes the reasoning further":
        "Profond — pousse davantage la réflexion",
    "Off (headset)":
        "Désactivé (casque)",
    "Nothing holds the microphone back: it always reaches the model, even while it speaks. Pick this on a headset if it ignores you or you cannot get a word in. Never on speakers: the reply would cut itself off.":
        "Rien ne retient le microphone : il parvient toujours au modèle, même pendant qu'il parle. À choisir avec un casque s'il vous ignore ou si vous n'arrivez pas à placer un mot. Jamais sur haut-parleurs : la réponse s'interromprait elle-même.",
    "Headset, no calibration":
        "Casque, sans calibration",
    "Holds the microphone while a reply plays, and lets you in at the plain voice threshold. For a headset whose own echo is measured wrongly (typically USB).":
        "Retient le microphone pendant une réponse, et vous laisse entrer au simple seuil de voix. Pour un casque dont l'écho est mal mesuré (typiquement USB).",
    "Speakers":
        "Haut-parleurs",
    "Holds the microphone while a reply plays and measures the echo to tell it from your voice. Pick this when the sound comes out of speakers.":
        "Retient le microphone pendant une réponse et mesure l'écho pour le distinguer de votre voix. À choisir quand le son sort de haut-parleurs.",
    "Conversation":
        "Conversation",
    "Anti-echo":
        "Anti-écho",
    "How much louder than the assistant's own echo you have to be for it to stop and listen.\n\nOn a headset this does nothing — nothing leaks back, so speaking interrupts as it always did. On speakers it is your room and your volume that decide: too low and the reply cuts itself off every time, too high and you cannot interrupt it at all. Every reply prints the two numbers to set it from (🔉 echo peak · loudest) in the terminal.\n\nWhen no value works — on a laptop's speakers a voice can beat the echo by as little as ×1.2 — the fix is a headset, or echo cancellation (PipeWire's libpipewire-module-echo-cancel).":
        "De combien vous devez être plus fort que l'écho de l'assistant pour qu'il s'arrête et vous écoute.\n\nAvec un casque, cela ne change rien — rien ne revient, donc parler interrompt comme toujours. Avec des haut-parleurs, ce sont votre pièce et votre volume qui décident : trop bas et la réponse s'interrompt toute seule à chaque fois, trop haut et vous ne pouvez plus l'interrompre. Chaque réponse affiche les deux valeurs pour le régler (🔉 écho max · le plus fort) dans le terminal.\n\nQuand aucune valeur ne convient — sur les haut-parleurs d'un portable, une voix peut ne dépasser l'écho que de ×1,2 — la solution est un casque, ou l'annulation d'écho (libpipewire-module-echo-cancel de PipeWire).",
    "Cutting in (× the echo)":
        "Interruption (× l'écho)",
    "The text is copied and pasted where the cursor was when the conversation ended — no Enter, and no chance to ask for another version or go back to the conversation. Off by default: talk mode otherwise never types anything you have not accepted.":
        "Le texte est copié et collé là où se trouvait le curseur quand la conversation s'est terminée — sans Entrée, et sans possibilité de demander une autre version ni de revenir à la conversation. Désactivé par défaut : sinon le mode conversation ne tape jamais rien que vous n'ayez accepté.",
    "Instructions":
        "Instructions",
    "Standing instructions for the conversation: language, persona, tone, the kind of work you usually discuss. Added to the built-in prompt rather than replacing it, and read by both engines. Leave empty for the default.":
        "Instructions permanentes pour la conversation : langue, personnalité, ton, le genre de travail dont vous parlez d'habitude. Ajoutées au prompt intégré au lieu de le remplacer, et lues par les deux moteurs. Laissez vide pour le comportement par défaut.",
    "Example:\nYou always answer in French, simply and without formality. The context is technical: be precise, and ask for a clarification rather than guess. The only English words are technical terms.":
        "Exemple :\nTu réponds toujours en français, simplement et sans formalisme. Le contexte est technique : sois précis, et demande une précision plutôt que de deviner. Les seuls mots anglais sont les termes techniques.",
    "Screen context":
        "Contexte d'écran",
    "Most of what you are about to dictate is already on your screen. The focused window is captured when the session starts, in parallel with your first sentence, and described by the vision model; <b>S</b> looks again once something has changed. {ai_key} and the companion always capture; the box below only decides it for {talk_key}, and is off by default. Each time, that window leaves the machine.":
        "L'essentiel de ce que vous allez dicter est déjà à l'écran. La fenêtre au premier plan est capturée au début de la session, en parallèle de votre première phrase, et décrite par le modèle de vision ; <b>S</b> regarde à nouveau quand quelque chose a changé. {ai_key} et le compagnon capturent toujours ; la case ci-dessous ne décide que pour {talk_key}, et elle est décochée par défaut. À chaque fois, cette fenêtre quitte la machine.",
    "The focused window (recommended)":
        "La fenêtre au premier plan (recommandé)",
    "A box around the cursor (X11 / Hyprland)":
        "Un cadre autour du curseur (X11 / Hyprland)",
    "The whole screen":
        "Tout l'écran",
    "Cropping is what makes the text readable: an image gets a fixed token budget whatever its size, so a whole 4K desktop comes back as confident nonsense. A session that cannot do the framing you pick falls back to the whole screen and says so.":
        "Le recadrage rend le texte lisible : une image reçoit un budget de jetons fixe quelle que soit sa taille, donc un bureau 4K entier revient en absurdités assurées. Une session qui ne peut pas faire le cadrage choisi retombe sur tout l'écran et le dit.",
    "Capture":
        "Capture",
    "Box width (px)":
        "Largeur du cadre (px)",
    "Longest edge of the uploaded image — what keeps a 4K capture small. 0 uploads it as captured.":
        "Plus grand côté de l'image envoyée — ce qui garde une capture 4K petite. 0 l'envoie telle que capturée.",
    "Downscale to (px)":
        "Réduire à (px)",
    "Capturing a window copies the image to the clipboard on niri, which has no way to skip it. Off by default — the session ends by leaving its generated text there anyway, so the only loss is whatever you had copied a minute ago. Text only.":
        "Capturer une fenêtre copie l'image dans le presse-papiers sous niri, qui ne permet pas de l'éviter. Désactivé par défaut — la session se termine de toute façon en y laissant son texte généré, donc la seule perte est ce que vous aviez copié il y a une minute. Texte seulement.",
    "Timings, prompts and the turn-detection thresholds live under <tt>[talk]</tt> in config.toml.":
        "Les durées, les prompts et les seuils de détection de fin de tour se trouvent sous <tt>[talk]</tt> dans config.toml.",
    "Ending turns on meaning needs onnxruntime (~200 MB), plus an 8 MB model downloaded the first time it runs. Without it, turns keep ending on silence.":
        "Terminer les tours sur le sens nécessite onnxruntime (~200 Mo), plus un modèle de 8 Mo téléchargé au premier usage. Sans lui, les tours continuent de se terminer sur le silence.",
    " · pasted without review":
        " · collé sans relecture",
    "Provider keys":
        "Clés des fournisseurs",
    "Logs":
        "Journaux",
    "Everything Loquivox prints goes to a log file too, so a session started at login leaves a trace. View shows the last lines live, unredacted, for your own eyes; Save writes the whole log with keys, names and addresses removed, ready to attach to an issue.":
        "Tout ce que Loquivox affiche va aussi dans un fichier journal, donc une session lancée à l'ouverture laisse une trace. Afficher montre les dernières lignes en direct, non expurgées, pour vos yeux seuls ; Enregistrer écrit le journal complet sans clés, noms ni adresses, prêt à joindre à une issue.",
    "The whole log with keys, names and addresses removed — the file to attach to a bug report":
        "Le journal complet sans clés, noms ni adresses — le fichier à joindre à un rapport de bug",
    "Log file":
        "Fichier journal",
    "Bug report":
        "Rapport de bug",
    "One text with the version, system, tools, config, key presence and the recent log. API keys, your user name, home directory, e-mail and IP addresses are removed; what you said is replaced by its length unless you choose to include it. You read it before anything is copied or saved.":
        "Un texte avec la version, le système, les outils, la configuration, la présence des clés et le journal récent. Les clés d'API, votre nom d'utilisateur, votre dossier personnel, votre e-mail et vos adresses IP sont retirés ; ce que vous avez dit est remplacé par sa longueur sauf si vous choisissez de l'inclure. Vous le lisez avant que quoi que ce soit soit copié ou enregistré.",
    "Report":
        "Rapport",
    "Click <b>⌨ Set</b> then press a key or combo (e.g. Alt+Space) — or type space-separated specs like <tt>ALT+SPACE F3</tt> (first = primary, rest = aliases; combos join with <tt>+</tt>).":
        "Cliquez sur <b>⌨ Définir</b> puis appuyez sur une touche ou un combo (p. ex. Alt+Espace) — ou tapez des spécifications séparées par des espaces comme <tt>ALT+SPACE F3</tt> (première = principale, les autres = alias ; les combos se joignent avec <tt>+</tt>).",
    "✓ Saved and applied — your new hotkeys work right away.":
        "✓ Enregistré et appliqué — vos nouveaux raccourcis fonctionnent tout de suite.",
    "Loquivox Settings":
        "Réglages de Loquivox",
    "Tools":
        "Outils",
    "Flow":
        "Déroulé",
    "Screen":
        "Écran",
    "General":
        "Général",
    "Voice & thinking":
        "Voix et réflexion",
    "Presence":
        "Présence",
    "Memory":
        "Mémoire",
    "Sound":
        "Son",
    "Microphone & music":
        "Micro et musique",
    "Echo guard":
        "Anti-écho",
    "Spoken voice":
        "Voix lue",
    "Writing & screen":
        "Écriture et écran",
    "Models by role":
        "Modèles par rôle",
    "Keys":
        "Clés",
    "Nothing to set here yet.":
        "Rien à régler ici pour l'instant.",
    "Models & keys":
        "Modèles et clés",
    "Appearance":
        "Apparence",
    "Hotkeys":
        "Raccourcis",
    "Support":
        "Support",
    "Apply":
        "Appliquer",
    "<small>✓ All settings applied.</small>":
        "<small>✓ Tous les réglages sont appliqués.</small>",
    "Install openwakeword":
        "Installer openwakeword",
    "Silence when the screen is locked":
        "Mettre en silence quand l'écran est verrouillé",
    "Leave it alone":
        "Ne rien faire",
    "Resume playback afterwards":
        "Reprendre la lecture ensuite",
    "Lower every other sound, then restore it and resume":
        "Baisser tous les autres sons, puis les rétablir et reprendre",
    "Lower other sounds while the companion speaks":
        "Baisser les autres sons pendant que le compagnon parle",
    "Lower other sounds while I speak":
        "Baisser les autres sons pendant que je parle",
    "Same as chat":
        "Comme le chat",
    "▶ Test":
        "▶ Essayer",
    "⚠️ Both models need an id.":
        "⚠️ Les deux modèles ont besoin d'un identifiant.",
    "Show available hotkeys next to the waveform":
        "Afficher les raccourcis disponibles à côté de la forme d'onde",
    "Show the refinement level applied to the dictation":
        "Afficher le niveau de reformulation appliqué à la dictée",
    "Show the chat overlay (answers and conversation bubble)":
        "Afficher la bulle de chat (réponses et bulle de conversation)",
    "Hotkey tab at the top of the screen (hover to expand)":
        "Onglet de raccourcis en haut de l'écran (survolez pour le déplier)",
    "Listening…":
        "J'écoute…",
    "Fall back to whisper.cpp when a backend is unavailable":
        "Se rabattre sur whisper.cpp quand un backend est indisponible",
    "Install / download":
        "Installer / télécharger",
    "Press once to start, again to stop (instead of holding the key)":
        "Appuyer une fois pour démarrer, une autre pour arrêter (au lieu de maintenir la touche)",
    "<small>✓ Applied — press to start, press to stop.</small>":
        "<small>✓ Appliqué — appuyer pour démarrer, appuyer pour arrêter.</small>",
    "<small>✓ Applied — recording while the key is down.</small>":
        "<small>✓ Appliqué — enregistrement tant que la touche est enfoncée.</small>",
    "(chosen automatically)":
        "(choisi automatiquement)",
    "whisper.cpp: ❌ engine binary (<tt>whisper-cli</tt>) not found":
        "whisper.cpp : ❌ binaire du moteur (<tt>whisper-cli</tt>) introuvable",
    "⚠️ Saved. No usable backend — fallback will be used.":
        "⚠️ Enregistré. Aucun backend utilisable — le repli sera utilisé.",
    "⚠️ Saved. whisper.cpp isn’t ready — use “Install / download” above.":
        "⚠️ Enregistré. whisper.cpp n'est pas prêt — utilisez « Installer / télécharger » ci-dessus.",
    "Translate to":
        "Traduire en",
    "Format as structured text (paragraphs + bullet lists)":
        "Mettre en forme en texte structuré (paragraphes + listes à puces)",
    "Custom level prompt":
        "Prompt du niveau personnalisé",
    "Clear custom prompt":
        "Effacer le prompt personnalisé",
    "Keep typing in other apps while the conversation runs":
        "Continuer à taper dans d'autres applications pendant la conversation",
    "When I say so out loud (“vas-y”, “j'ai fini”, “that's it”)":
        "Quand je le dis à voix haute (« vas-y », « j'ai fini », « c'est tout »)",
    "When the assistant judges it has enough context":
        "Quand l'assistant juge avoir assez de contexte",
    "Paste the finished text straight away, without reviewing it":
        "Coller le texte terminé tout de suite, sans relecture",
    "Let the assistant see what you are working in":
        "Laisser l'assistant voir sur quoi vous travaillez",
    "Put back what was on the clipboard afterwards":
        "Remettre ensuite ce qu'il y avait dans le presse-papiers",
    "Install onnxruntime?":
        "Installer onnxruntime ?",
    "Show the keys":
        "Afficher les clés",
    "View log…":
        "Voir le journal…",
    "Save log…":
        "Enregistrer le journal…",
    "Start something":
        "Démarrer quelque chose",
    "Dictation records while its key is down — or press once to start and again to stop, see the Dictation tab. The others open a conversation, or the companion's.":
        "La dictée enregistre tant que sa touche est enfoncée — ou appuyez une fois pour démarrer et une autre pour arrêter, voir l'onglet Dictée. Les autres ouvrent une conversation, ou celle du compagnon.",
    "Act on a session":
        "Agir sur une session",
    "Pressed once, acting on whatever is already running.":
        "Appuyée une fois, elle agit sur ce qui tourne déjà.",
    "unbound":
        "non assignée",
    "Press a key to bind it (no need to know its name)":
        "Appuyez sur une touche pour l'assigner (inutile d'en connaître le nom)",
    "<small>✓ {engine} — takes effect on the next talk session.</small>":
        "<small>✓ {engine} — effectif à la prochaine session de conversation.</small>",
    "⏳ Listing {provider} models…":
        "⏳ Liste des modèles de {provider}…",
    "Auto":
        "Auto",
    "How the dictation key ({key}) starts and stops a recording.":
        "Comment la touche de dictée ({key}) démarre et arrête un enregistrement.",
    "⚠️ Saved, but <b>{name}</b> is unavailable — fallback will be used.":
        "⚠️ Enregistré, mais <b>{name}</b> est indisponible — le repli sera utilisé.",
    "⏳ Installing {human} ({pip_name})…":
        "⏳ Installation de {human} ({pip_name})…",
    "see logs":
        "voir les journaux",
    "<small><b>Refinement level:</b> {name}</small>":
        "<small><b>Niveau de reformulation :</b> {name}</small>",
    "translate → {lang}":
        "traduire → {lang}",
    "level {level} ({name})":
        "niveau {level} ({name})",
    "<small>✓ Applied live — {desc}.</small>":
        "<small>✓ Appliqué en direct — {desc}.</small>",
    "{talk_key} only: the briefing ends in a written text, and this is how hard the assistant questions you before writing. {ai_key} is an open chat and never reads it. Applies on every engine.":
        "{talk_key} seulement : le briefing se termine par un texte écrit, et ceci règle à quel point l'assistant vous questionne avant d'écrire. {ai_key} est un chat ouvert et ne le lit jamais. S'applique à tous les moteurs.",
    "How far it digs ({talk_key})":
        "Jusqu'où creuser ({talk_key})",
    "Cascade engine, {ai_key} and {talk_key}. The Realtime and Live engines close your turns on their own server, so this does nothing there. If onnxruntime is missing, ticking asks before installing it.":
        "Moteur cascade, {ai_key} et {talk_key}. Les moteurs Realtime et Live terminent vos tours sur leur propre serveur, donc cela ne change rien là. Si onnxruntime manque, cocher demande avant de l'installer.",
    "A session normally takes the keyboard for itself while it waits on you, so nothing you press reaches the app underneath.\n\nTicked, it leaves it alone: you carry on typing, and your shortcuts keep working. Every key then lands in that app too, so the session answers to {talk_key} / {ai_key} alone — pressed again, it ends the session — and Space / Enter / Esc / S / T no longer do anything to it. The review of the finished text still takes the keyboard: it is a question with an answer.\n\nThe bubble carries the same switch, so it can also be flipped mid-conversation — and what you flip it to lands back here.":
        "Une session prend normalement le clavier pour elle pendant qu'elle vous attend, donc rien de ce que vous tapez n'atteint l'application dessous.\n\nCoché, elle le laisse tranquille : vous continuez à taper, et vos raccourcis continuent de fonctionner. Chaque touche arrive alors aussi dans cette application, donc la session ne répond qu'à {talk_key} / {ai_key} — appuyé à nouveau, il termine la session — et Espace / Entrée / Échap / S / T ne lui font plus rien. La relecture du texte terminé prend toujours le clavier : c'est une question qui attend une réponse.\n\nLa bulle porte le même interrupteur, donc on peut aussi le basculer en pleine conversation — et ce que vous choisissez revient ici.",
    "Ending the briefing ({talk_key})":
        "Terminer le briefing ({talk_key})",
    "Enter always writes the text — or the talk key itself, with the keyboard left free. That one can't be turned off, so there is always a way out. These are extras. A chat ({ai_key}) ends on the key alone: it writes through the tool, whenever you ask, without ending.":
        "Entrée écrit toujours le texte — ou la touche de conversation elle-même, avec le clavier libre. Celle-là ne peut pas être désactivée, il y a donc toujours une sortie. Voici des options en plus. Un chat ({ai_key}) se termine sur la touche seule : il écrit via l'outil, quand vous le demandez, sans se terminer.",
    "{label} — installing onnxruntime…":
        "{label} — installation d'onnxruntime…",
    " · {n} chars of instructions":
        " · {n} caractères d'instructions",
    "<small>✓ Applied — ends on: {ends}{instr_note}.</small>":
        "<small>✓ Appliqué — se termine sur : {ends}{instr_note}.</small>",
    "Stored in <tt>{v0}</tt> (chmod 600) and loaded at startup, so they survive a reboot. Applied live on save.":
        "Stockées dans <tt>{v0}</tt> (chmod 600) et chargées au démarrage, elles survivent donc à un redémarrage. Appliquées en direct à l'enregistrement.",
    "(set via environment — type to override)":
        "(défini par l'environnement — saisissez pour remplacer)",
    "<small>✓ Saved {n} key(s) (chmod 600) &amp; applied live.</small>":
        "<small>✓ {n} clé(s) enregistrée(s) (chmod 600) &amp; appliquée(s) en direct.</small>",
    "(disabled via LOQUIVOX_LOG_FILE)":
        "(désactivé via LOQUIVOX_LOG_FILE)",
    "<small>Without the tray: <tt>loquivox --report</tt> · version {v0}</small>":
        "<small>Sans la barre système : <tt>loquivox --report</tt> · version {v0}</small>",
    "⌨ Set":
        "⌨ Définir",
    "⌨ Press a key for <b>{label}</b>… (6s)":
        "⌨ Appuyez sur une touche pour <b>{label}</b>… (6 s)",
    "<small>⚠️ Applied — {unhappy} setting(s) need a look; their tab says which.</small>":
        "<small>⚠️ Appliqué — {unhappy} réglage(s) à vérifier ; leur onglet dit lesquels.</small>",
    "<small>✓ openwakeword installed — Apply to load it.</small>":
        "<small>✓ openwakeword installé — Appliquer pour le charger.</small>",
    "<small>⚠️  No preview for {voice} — it is a Live-only voice.</small>":
        "<small>⚠️  Pas d'aperçu pour {voice} — c'est une voix réservée à Live.</small>",
    " · models: {doc}":
        " · modèles : {doc}",
    "whisper.cpp: ✅ model <b>{model}</b> downloaded (offline ready)":
        "whisper.cpp : ✅ modèle <b>{model}</b> téléchargé (prêt hors ligne)",
    "whisper.cpp: ⬇ model <b>{model}</b> not downloaded yet":
        "whisper.cpp : ⬇ modèle <b>{model}</b> pas encore téléchargé",
    "whisper.cpp: ⏳ downloading model <b>{model}</b>…":
        "whisper.cpp : ⏳ téléchargement du modèle <b>{model}</b>…",
    "✓ Applied live — backend <b>{name}</b>.":
        "✓ Appliqué en direct — backend <b>{name}</b>.",
    " (offers to install onnxruntime)":
        " (propose d'installer onnxruntime)",
    "Enter":
        "Entrée",
    "spoken":
        "parlé",
    "assistant":
        "assistant",
    "⚠️ No key captured (timed out or no input device).":
        "⚠️ Aucune touche captée (délai dépassé ou aucun périphérique d'entrée).",
    "<small>❌ wake word: {v0}</small>":
        "<small>❌ mot de réveil : {v0}</small>",
    "{v0} (not connected)":
        "{v0} (non connecté)",
    "⚠️ {human} needs its API key — set <b>{env_key}</b> in “Models & keys”, then Apply. (Offline fallback used meanwhile.)":
        "⚠️ {human} a besoin de sa clé d'API — définissez <b>{env_key}</b> dans « Modèles et clés », puis Appliquer. (Repli hors ligne utilisé en attendant.)",
    "<small>❌ install failed: {v0}</small>":
        "<small>❌ échec de l'installation : {v0}</small>",
    "Cascade — transcribe, answer, speak":
        "Cascade — transcrire, répondre, parler",
    "Realtime — speech to speech (OpenAI)":
        "Realtime — parole à parole (OpenAI)",
    "Live — OpenAI voice, delegated thinking":
        "Live — voix OpenAI, réflexion déléguée",
    "Auto — follow the chat provider":
        "Auto — suivre le fournisseur de chat",
    "Your backend — the chat model, with plugins":
        "Votre backend — le modèle de chat, avec plugins",
    "OpenAI's backend — named tool calls":
        "Backend d'OpenAI — appels d'outils nommés",
    "Yours — the search plugin, your provider":
        "La vôtre — le plugin de recherche, votre fournisseur",
    "OpenAI's — their search, run on their side":
        "La leur — leur recherche, exécutée de leur côté",
    "⚠️ Could not list models — {v0}":
        "⚠️ Impossible de lister les modèles — {v0}",
    "whisper.cpp: ❌ download failed: {e}":
        "whisper.cpp : ❌ échec du téléchargement : {e}",
    "❌ {human} install failed — {msg}":
        "❌ échec de l'installation de {human} — {msg}",
    "{label} (ready)":
        "{label} (prêt)",
    "{label} — install failed: {msg}":
        "{label} — échec de l'installation : {msg}",
    "✓ Captured <b>{name}</b> for {label} — click <b>Apply</b> to save.":
        "✓ <b>{name}</b> capté pour {label} — cliquez sur <b>Appliquer</b> pour enregistrer.",
    "model download failed: {e}":
        "échec du téléchargement du modèle : {e}",
    "❌ invalid binding '{tok}' for {mode_id}.":
        "❌ assignation invalide « {tok} » pour {mode_id}.",
    "Loquivox":
        "Loquivox",
    "Clear History":
        "Effacer l'historique",
    "Show chat":
        "Afficher le chat",
    "Settings…":
        "Réglages…",
    "Quit":
        "Quitter",
    "(No History)":
        "(Aucun historique)",
    "Infinite polar blues echoing the cosmos and the soft light of evening snow.":
        "Bleus polaires infinis qui font écho au cosmos et à la lumière douce de la neige du soir.",
    "Intense, blazing crimson radiates energy; a bold and dramatic command of attention.":
        "Un cramoisi intense et flamboyant qui rayonne d'énergie ; une présence audacieuse et dramatique qui impose l'attention.",
    "Delicate pastel petals and whimsical sky blues, bringing a soft, elegant charm.":
        "Pétales pastel délicats et bleus de ciel fantaisistes, pour un charme doux et élégant.",
    "Mysterious abyssal depths where cyan phosphorescence meets resilient silent strength.":
        "Profondeurs abyssales mystérieuses où la phosphorescence cyan rencontre une force silencieuse et résistante.",
    "Lush, dark greens of ancient forests whispering beneath the mint-bright aurora.":
        "Verts sombres et luxuriants de forêts anciennes qui murmurent sous l'aurore d'un vert menthe éclatant.",
    "Mathematically balanced depths of solarized teal, a classic of modern interface harmony.":
        "Profondeurs sarcelle solarisées à l'équilibre mathématique, un classique de l'harmonie des interfaces modernes.",
    "Warm engineered earth tones of copper and cream, evoking a retro-industrial rustic elegance.":
        "Tons de terre chauds et travaillés de cuivre et de crème, évoquant une élégance rustique rétro-industrielle.",
    "Vibrant high-contrast purple and deep ink-blue, capturing the electric glow of a bioluminescent forest.":
        "Violet vibrant très contrasté et bleu encre profond, qui capturent l'éclat électrique d'une forêt bioluminescente.",
    "Sun-drenched golden sands meet the smoky blue of midnight tides and crystalline waters.":
        "Sables dorés gorgés de soleil qui rencontrent le bleu fumé des marées de minuit et des eaux cristallines.",
    "{tool} failed: {why}": "{tool} a échoué : {why}",
    "{tool} was done, but I could not finish the rest in time.": "{tool} a été fait, mais je n'ai pas pu terminer la suite à temps.",
    "I could not finish that in time.": "Je n'ai pas pu terminer à temps.",
    "Run {tool} ({server}) with {args}? Say yes to go ahead.":
        "Lancer {tool} ({server}) avec {args} ? Dis oui pour que j'y aille.",
    "no arguments": "aucun argument",
    "Like the conversation": "Comme la conversation",
    "The companion always talks through the Live engine. Each setting below follows the Conversation tab until you give it a value of its own.":
        "Le compagnon parle toujours par le moteur Live. Chaque réglage ci-dessous suit l'onglet Conversation tant que vous ne lui donnez pas de valeur propre.",
    "Empty = the instructions of the Conversation tab. Given here, they replace those for the companion.":
        "Vide = les instructions de l'onglet Conversation. Renseignées ici, elles les remplacent pour le compagnon.",
    "On waking, the companion captures the focused window and has it described — as the screen framing set in <b>Conversation › Screen</b> says.":
        "Au réveil, le compagnon capture la fenêtre active et la fait décrire, avec le cadrage réglé dans <b>Conversation › Écran</b>.",
    "When the companion listens, and when it keeps quiet.":
        "Quand le compagnon écoute, et quand il se tait.",
    "Enabled": "Activé",
    "Lock": "Verrouillage",
    "Listen when Loquivox launches": "Écouter au lancement de Loquivox",
    "Seconds without a word heard before a notification may open a conversation by itself":
        "Secondes sans parole entendue avant qu'une notification puisse ouvrir seule une conversation",
    "Delay before announcing (s)": "Délai avant d'annoncer (s)",
    "The silence key is set in the <b>Hotkeys</b> tab (<i>Companion</i>, F7 by default); it applies right away.":
        "La touche de silence se règle dans l'onglet <b>Raccourcis</b> (<i>Compagnon</i>, F7 par défaut) ; elle s'applique tout de suite.",
    "Wakes the companion": "Réveille le compagnon",
    '<small>{v0} filled in — click Apply to save it; it takes effect on the next talk session.</small>':
        "<small>{v0} préparé — cliquez sur Appliquer pour l'enregistrer ; il prend effet à la prochaine conversation.</small>",
    '<small>✓ Saved — applies from the next recording.</small>':
        '<small>✓ Enregistré — appliqué dès le prochain enregistrement.</small>',
    '<small>✓ Saved — applies from the next reply.</small>':
        '<small>✓ Enregistré — appliqué dès la prochaine réponse.</small>',
    '<small>✓ Saved — used from the next recording.</small>':
        '<small>✓ Enregistré — utilisé dès le prochain enregistrement.</small>',
    '<small>✓ Saved.</small>':
        '<small>✓ Enregistré.</small>',
    'A separate engine that speaks written text, so it works everywhere and can be local. It is not the Realtime or Live voice (Conversation tab): those are the model speaking for itself.':
        "Un moteur à part qui lit du texte écrit : il marche partout et peut être local. Ce n'est pas la voix de Realtime ou de Live (onglet Conversation) : celles-là sont le modèle qui parle de lui-même.",
    'Answers':
        'Réponses',
    'Anti-echo and cutting in are set in <b>Sound › Echo guard</b>.':
        "L'anti-écho et la coupure de parole se règlent dans <b>Son › Anti-écho</b>.",
    'Cascade engine only: Realtime and Live speak with their own voice. Unticked, a conversation speaks only while the switch above is on.':
        "Moteur cascade seulement : Realtime et Live parlent avec leur propre voix. Décoché, une conversation ne parle que si l'interrupteur ci-dessus est activé.",
    'Chat answers':
        'Réponses du chat',
    'Cloud cascade — live transcription, cloud voice, and your chat provider answers (set in Models by role). The default, ~1.8s before you hear a reply.':
        "Cascade cloud — transcription en direct, voix cloud, et votre fournisseur de chat répond (réglé dans Modèles par rôle). Le choix par défaut, ~1,8 s avant d'entendre une réponse.",
    'Conversations':
        'Conversations',
    'Fill the engine, the transcription and the voice in one click — each lives on its own tab. Hover a preset to see what it picks; nothing is saved until you click Apply, and everything stays editable.':
        "Remplit le moteur, la transcription et la voix en un clic — chacun a son propre onglet. Survolez un préréglage pour voir ce qu'il choisit ; rien n'est enregistré avant de cliquer sur Appliquer, et tout reste modifiable.",
    'Final text':
        'Texte final',
    'How {ai_key} and {talk_key} conversations behave. "How far it digs" only applies to {talk_key}; "End my turns on meaning" only to the cascade engine. Reading replies aloud is set in Sound.':
        "Comment se comportent les conversations {ai_key} et {talk_key}. « Jusqu'où il creuse » ne vaut que pour {talk_key} ; « Finir mes tours au sens » que pour le moteur cascade. La lecture des réponses à voix haute se règle dans Son.",
    'Memory judge':
        'Juge de la mémoire',
    'One row per job, a provider then a model. The agent answers {ai_key}, {talk_key} and the companion and runs their tools; the chat model writes the final text and refines your dictation; vision reads the screen; search looks facts up on the web; the memory judge decides what the companion no longer repeats. A row left on “Same as chat” follows the chat model.':
        "Une ligne par tâche, un fournisseur puis un modèle. L'agent répond à {ai_key}, {talk_key} et au compagnon et lance leurs outils ; le modèle de chat écrit le texte final et raffine votre dictée ; la vision lit l'écran ; la recherche cherche les faits sur le web ; le juge de la mémoire décide de ce que le compagnon ne répète plus. Une ligne laissée sur « Comme le chat » suit le modèle de chat.",
    'Read chat answers aloud':
        'Lire les réponses du chat à voix haute',
    'Read conversation replies aloud, even with the switch above off':
        "Lire les réponses des conversations à voix haute, même avec l'interrupteur ci-dessus coupé",
    'Search is switched on in <b>Tools › Search</b>.':
        "La recherche s'active dans <b>Outils › Recherche</b>.",
    'The ears: what turns your voice into text, for every mode. Applied live. The microphone is set in <b>Sound</b>.':
        'Les oreilles : ce qui transforme votre voix en texte, pour tous les modes. Appliqué en direct. Le micro se règle dans <b>Son</b>.',
    "The input every mode listens to: dictation, conversations and the companion's wake word. Takes effect at the next recording.":
        "L'entrée que tous les modes écoutent : dictée, conversations et mot de réveil du compagnon. Prend effet au prochain enregistrement.",
    'The provider and the model are set in <b>Models &amp; keys › Models by role</b>.':
        'Le fournisseur et le modèle se règlent dans <b>Modèles et clés › Modèles par rôle</b>.',
    'The same switch as {key}. It also makes conversations speak their replies when the box below is not ticked.':
        "Le même interrupteur que {key}. Il fait aussi parler les conversations quand la case ci-dessous n'est pas cochée.",
    'Vision':
        'Vision',
    'What keeps the assistant from hearing its own voice, and how you cut in on it. It applies to every conversation engine and to the companion.':
        "Ce qui empêche l'assistant de s'entendre lui-même, et la façon dont vous lui coupez la parole. Vaut pour tous les moteurs de conversation et pour le compagnon.",
    '⚠️ The search needs a model id.':
        "⚠️ La recherche a besoin d'un identifiant de modèle.",
    '✓ Applied — chat {chat} · vision {vision} · agent {agent}.':
        '✓ Appliqué — chat {chat} · vision {vision} · agent {agent}.',
    '✓ {v0} models available on {provider}. Pick or type one, then Apply. An empty model follows the chat model.':
        '✓ {v0} modèles disponibles chez {provider}. Choisissez ou tapez-en un, puis Appliquer. Un modèle vide suit le modèle de chat.',
    '<small>✓ Advanced settings saved — used from the next session.</small>':
        '<small>✓ Réglages avancés enregistrés — pris en compte à la prochaine session.</small>',
    '<small>✓ Servers updated — their state follows below.</small>':
        '<small>✓ Serveurs mis à jour — leur état suit ci-dessous.</small>',
    'A pause this long ends your turn':
        'Une pause de cette durée met fin à votre tour',
    'Advanced':
        'Avancé',
    'Agent rounds (max)':
        "Tours de l'agent (max)",
    'Agent time limit (s)':
        "Durée limite de l'agent (s)",
    'Click on the bubble':
        'Clic sur la bulle',
    'Companion backend model':
        'Modèle backend du compagnon',
    'Companion delegates thinking to':
        'Le compagnon délègue la réflexion à',
    'Companion instructions':
        'Instructions du compagnon',
    'Companion web search':
        'Recherche web du compagnon',
    'Companion — silence':
        'Compagnon — silence',
    'Companion — wake':
        'Compagnon — réveil',
    "Companion's Live voice":
        'Voix Live du compagnon',
    'Conversation ends after (s)':
        'La conversation se termine après (s)',
    'Conversation — chat':
        'Conversation — discussion',
    'Conversation — write':
        'Conversation — rédaction',
    'How a conversation is held':
        'Comment la conversation est tenue',
    'How long the sound stays lowered after the speech stops':
        'Durée pendant laquelle le son reste baissé après la fin de la parole',
    'How the companion talks and thinks':
        'Comment le compagnon parle et réfléchit',
    'Input device':
        "Périphérique d'entrée",
    'Interface language':
        "Langue de l'interface",
    'Let the model look at the window again when asked':
        'Laisser le modèle regarder de nouveau la fenêtre quand on le demande',
    'Live model':
        'Modèle Live',
    'Look at the screen':
        "Regarder l'écran",
    'Lowered sound returns after (s)':
        'Le son baissé revient après (s)',
    "Minimum voice level that counts as speech; the room's noise raises it":
        'Niveau de voix minimal qui compte comme de la parole ; le bruit de la pièce le relève',
    'Open config.toml':
        'Ouvrir config.toml',
    'Outside a recording':
        "Hors d'un enregistrement",
    'Pause that ends a turn (ms)':
        'Pause qui termine un tour (ms)',
    'Pin the chat':
        'Épingler le chat',
    'Realtime transcription':
        'Transcription Realtime',
    'Rounds of tool calls the agent may chain in one answer':
        "Tours d'appels d'outils que l'agent peut enchaîner dans une réponse",
    'Said on their own, these close the conversation without a reply':
        'Dites seules, ces phrases ferment la conversation sans réponse',
    'Seconds of silence, both ways, that end a conversation':
        'Secondes de silence, dans les deux sens, qui terminent une conversation',
    'Separated by commas; accents and punctuation are ignored.':
        'Séparées par des virgules ; les accents et la ponctuation sont ignorés.',
    'Shorter sounds are ignored':
        'Les sons plus courts sont ignorés',
    'Shortest speech (ms)':
        'Parole la plus courte (ms)',
    'Shown in':
        'Affichée en',
    'Sound — read aloud':
        'Son — lecture à voix haute',
    'Speech engine':
        'Moteur de la voix',
    'Speech voice':
        'Voix lue',
    'Stop phrases':
        "Phrases d'arrêt",
    "The Live engine's voice model":
        'Le modèle vocal du moteur Live',
    'Time limit for one answer, tools included':
        "Durée limite d'une réponse, outils compris",
    'Transcribes your side of a Realtime conversation':
        'Transcrit votre côté d’une conversation Realtime',
    'Transcription model':
        'Modèle de transcription',
    'Voice level':
        'Niveau de voix',
    'Wake word and bubble':
        "Mot d'éveil et bulle",
    'Web lookup':
        'Modèle de recherche',
    'While the companion talks':
        'Pendant que le compagnon parle',
    'Write on request':
        'Rédiger sur demande',
}
