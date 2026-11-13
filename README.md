# Loxone-Jarvis

Sprachassistent mit Jarvis-Persönlichkeit für ein Loxone-Haus. Wakeword-Mikrofone in den Räumen, freie Sätze für das ganze Haus, Antwort als Durchsage über den Loxone Audioserver. Ein Repo, eine Coolify-Anwendung, laufende Kosten nur bei Google (Gemini).

- **Hören:** Seeed XIAO ESP32-S3 Sense mit „Jarvis“ auf dem Board. Erst der Satz danach geht an Jarvis.
- **Verstehen:** Spracherkennung lokal (faster-whisper). Zu Google geht nur der erkannte Text.
- **Denken:** Gemini (Standard `gemini-3.8-flash`, im Drop-down wählbar) mit den freigegebenen Bausteinen. Google-Suche nur bei offenen Fragen.
- **Handeln:** Miniserver über die Websocket-Schnittstelle, nur freigegebene Bausteine. Alarm, Schloss und Garage erst nach gesprochenem „Ja“. „Spiel Jazz im Wohnzimmer“ sucht am Audioserver eine Spotify-Playlist und startet sie in der Zone. Spotify muss dort angemeldet sein.
- **Sprechen:** Piper lokal, als Durchsage über den Audioserver. Musik läuft danach weiter.
- **Tempo:** Licht, Dimmen, Rollos, Uhrzeit und Datum beantwortet Jarvis sofort ohne Gemini, trotzdem in seiner Persönlichkeit: Gemini formuliert dafür einmal einen Satzvorrat aus dem System-Prompt, Jarvis wechselt die Formulierungen ab und spricht sie mit derselben Stimme. Bei allen anderen Aktionen liefert Gemini die Bestätigung im selben Aufruf mit. Spracherkennung startet schon in der ersten Pause, die Verbindung zu Gemini bleibt warm.
- **Geräteverständnis:** Jarvis kennt Licht, Dimmer, Farbe, Schalter, Taster, Rollos mit Lamellen, Fenster, Tore, Raumregler, Alarm, Auswahl- und Wertbausteine sowie Sensoren (Temperatur, Verbrauch, Fensterüberwachung, Anwesenheit, Texte). Zustände liest er so, wie man sie sagt („40 % geschlossen, Lamellen 50 %“, „2 offen, 1 gekippt“), und versteht unterschiedliche Formulierungen für dieselbe Aktion. Sensoren sind nur zum Ablesen und von Anfang an freigegeben. „Wie warm ist es im Bad/draußen?“ beantwortet er sofort. Fällt die Verbindung zum Miniserver kurz weg, wiederholt Jarvis den Befehl einmal.
- **Timer und Erinnerungen:** „Stell mir einen Timer in 1 Stunde, da ist das Essen fertig“, „Erinnere mich um 18 Uhr daran, dass ich den Müll rausbringe“, „Wie lange läuft der Timer noch?“ und „Brich den Timer ab“ laufen sofort ohne Gemini (Dauern wie „eine halbe Stunde“, „anderthalb Stunden“, „1 Stunde 30“, Uhrzeiten wie „halb sieben“). Ist es so weit, formuliert Jarvis die Ansage in seiner Persönlichkeit mit dem genannten Anlass, etwa „Verzeihung, Sir, das Essen dürfte jetzt fertig sein.“
- **Gespräche:** Passt eine Folgeaktion, fragt Jarvis nach, etwa abends nach dem Fernsehlicht „Soll ich außerdem die Beschattung herunterfahren?“. Ein einfaches „Ja“ (auch „gerne“, „klar“) führt sie sofort lokal aus, „Nein“ merkt er sich für einige Stunden. Bei Sofortbefehlen fragt Jarvis Gemini parallel im Hintergrund, die Frage folgt direkt nach der Bestätigung. Stufe zurückhaltend, aktiv oder aus auf der KI-Seite.
- **Lichtbausteine:** Jarvis kennt die Lichtkreise eines Lichtbausteins, ihre Lichtart (direkt/indirekt, aus Loxone und den Namen, auf der Loxone-Seite übersteuerbar) und die Stimmungen. „Mach die indirekte Beleuchtung im Wohnzimmer am Fernseher an“ oder „Stimmung Fernsehen“ schaltet er sofort. „Struktur herunterladen“ auf der Loxone-Seite liefert die bereinigte Loxone-Struktur für die Fehlersuche.
- **Feinabstimmung (KI-Seite):** Satzende-Pause, Vorab-Erkennung, Nachfrage-Fenster, Sofortantworten an/aus, Nachdenken von Gemini und frühes Hinhören in Live. Die Loxone-Seite zeigt beim letzten Satz, wie lange Erkennung, KI und Stimme gebraucht haben.
- **Stimmen:** 29 Piper-Modelle, davon mehrere mit verschiedenen Sprechern (Thorsten emotional, MLS, Semaine, VCTK, Aru). Dazu kommen drei Jarvis-Stimmen, beide nur für private Nutzung: auf Deutsch „Jarvis“ von [ufozone](https://huggingface.co/ufozone/piper-de_DE-jarvis-medium) in medium und high (CC BY-NC-SA 4.0), auf Englisch die an den Film angelehnte Stimme von [jgkawell](https://huggingface.co/jgkawell/jarvis) in medium und high (laut Autor MIT, aus Spielaufnahmen trainiert). Auswahl und Probehören auf der KI-Seite.
- **Persönlichkeit:** Jarvis spricht als britischer Butler, ruhig, knapp und mit trockenem Humor. Erkennt er die Stimme nicht, nutzt er die Anrede aus „Haushalt“ (Sir, Ma'am oder keine). Ein eigener Text auf der KI-Seite ersetzt die Vorgabe.
- **Live:** Menüpunkt „Live“ auf Tablet oder Handy. Nur Hintergrund und Uhr, Jarvis hört über das Mikrofon des Geräts auf „Jarvis“ und antwortet über dessen Lautsprecher, nicht über den Audioserver. Das Mikrofon ist nur offen, solange Live geöffnet ist. Tippen blendet das Menü ein.

## Aufbau

| Dienst | Aufgabe | Port |
|---|---|---|
| `jarvis` | Oberfläche, Mikrofone, Erkennung, Gemini, Loxone, Audioserver, Config-MCP | 8500 (direkt `http://<Server>:8500`, optional HTTPS über eine Domain), 8501 (HTTPS mit eigenem Zertifikat für Live), 8765 (nur für die Mikrofone im Hausnetz) |
| `piper` | Sprachausgabe, nur intern | 5000 |

Daten liegen im Volume `jarvis-data` (SQLite). Zugangsdaten sind dort mit AES-GCM verschlüsselt, der Schlüssel kommt aus `JARVIS_SECRET_KEY` und liegt nur in Coolify.

## Installation in Coolify

1. Neue Ressource → **Docker Compose** → dieses GitHub-Repo, Branch `main`, Datei `docker-compose.yml`.
2. Umgebungsvariable `JARVIS_SECRET_KEY` als Secret setzen, zum Beispiel aus `openssl rand -base64 48`. Den Wert aufbewahren: ohne ihn lassen sich die gespeicherten Zugänge nicht mehr entschlüsseln.
3. Keine Domain nötig. Nach dem Deploy die Oberfläche im Hausnetz unter `http://<IP-des-Servers>:8500` öffnen. Pair über USB funktioniert dort ebenfalls.
4. Deployen. Der erste Bau lädt Whisper-Modelle, das Sprechermodell und die Piper-Stimmen in die Images (einige Minuten, danach braucht der Betrieb kein Internet außer Gemini).
5. Die Seite öffnen und das Oberflächen-Passwort festlegen (mindestens 10 Zeichen).

Jarvis antwortet nur Adressen aus privaten Netzen, auch hinter dem Coolify-Proxy. Keine Portweiterleitung im Router einrichten. Wer zusätzlich sperren will: ausgehend nur `generativelanguage.googleapis.com` erlauben.

## Einrichten in der Oberfläche

1. **KI:** Gemini-API-Schlüssel aus Google AI Studio setzen, Modell wählen, „Verbindung prüfen“. Stimmen für Deutsch und Englisch wählen.
2. **Loxone:** In Loxone Config einen Benutzer `jarvis` **ohne Admin-Recht** anlegen, mit Zugriff auf die Räume und Bausteine, die Jarvis schalten soll. Adresse, Benutzer und Passwort eintragen, „Verbinden“. Danach in der Topologie Bausteine freigeben und Sprechweisen ergänzen. Alle Audioserver werden aus dem Miniserver erkannt. Jede Zone spricht über ihren eigenen Server, Spotify muss an jedem Server angemeldet sein, dessen Zonen Musik spielen sollen. Sind die Adressen aus Loxone Config vom Jarvis-Server nicht erreichbar, alle Audioserver-Adressen auf der Loxone-Seite mit Komma getrennt eintragen.
3. **Mikrofone:** WLAN (2,4 GHz) und die Adresse des Mini-Servers im Hausnetz eintragen. Bereiche anlegen: je Bereich eine Audio-Zone, das Hauptlicht und die Loxone-Räume.
4. **Pair:** Board per USB an den Rechner mit Chrome oder Edge, „Pair“. Über `http://<IP>:8500` lädt die Seite einmal `jarvis-pair.py`. Das Programm mit `python3 jarvis-pair.py` starten (Windows: `py -3 jarvis-pair.py`). Es öffnet eine lokale Seite, schreibt die Firmware, übergibt das WLAN, und Jarvis setzt einen eigenen Schlüssel. Danach USB ab, Board an ein USB-C-Netzteil im Raum, Bereich zuordnen.
5. **Übersicht:** Personen mit Anrede (Name, Sir, Mam) und Notizen anlegen, optional Stimmprofil mit drei Sätzen aufnehmen. Unter Haushalt den Standort des Hauses festlegen (Adresse suchen oder „Aktuellen Standort verwenden“), dazu Notizen und Zeitzone. Jarvis bezieht Wetter, Sonnenauf- und -untergang und Fragen zur Umgebung auf diesen Ort. Die Adresssuche fragt OpenStreetMap, nur beim Suchen.
6. Optional **Loxone Config · MCP:** In Config den MCP-Server in der Titelleiste einschalten und die Adresse eintragen. Dann legt Jarvis neue Logik an, nach Ansage und „Ja“. Bietet Config ein Werkzeug zum Speichern auf den Miniserver an, nutzt Jarvis es, sonst bleibt die Änderung im offenen Projekt.
7. Optional **Live auf Tablet oder Handy, als App:**
   - Einmal pro Gerät das Zertifikat von Jarvis installieren: `http://<IP-des-Servers>:8500/jarvis-ca.crt` öffnen.
     - **Android:** Einstellungen → Sicherheit → Verschlüsselung und Anmeldedaten → Zertifikat installieren → CA-Zertifikat → `jarvis-ca.crt`.
     - **iPhone/iPad:** Profil laden, unter Einstellungen → Allgemein → VPN und Geräteverwaltung installieren, dann unter Allgemein → Info → Zertifikatsvertrauenseinstellungen „Jarvis Hausnetz“ einschalten.
   - Danach `https://<IP-des-Servers>:8501/#live` öffnen (ohne Warnung), anmelden, Mikrofon erlauben.
   - **Als App:** Chrome „App installieren“ (auch über das Menü in Live, Tippen blendet es ein), Safari Teilen → „Zum Home-Bildschirm“. Die App heißt „Jarvis“, startet direkt in Live und im Vollbild.
   - Das Zertifikat deckt automatisch jede Adresse ab, unter der Jarvis geöffnet wurde. Ohne Installation geht Live auch mit einmal bestätigter Warnung, dann aber nicht als App.
   - Im Menü lässt sich ein Raum für Sätze ohne Raumangabe wählen. Timer und Erinnerungen aus Live klingeln auf dem Gerät, solange Live offen ist, sonst über den Audioserver.

## Mikrofon kaufen

**Seeed XIAO ESP32-S3 Sense**: bei Reichelt Artikel **406873** (mit Stiftleiste) oder **358353** (ohne Stiftleiste), dasselbe Board. Artikel **358354** ist das Board ohne Sense-Modul und hat **kein Mikrofon**.

- Die mitgelieferte Stabantenne auf den U.FL-Stecker setzen.
- Strom über USB-C. Bei PoE nur über einen Splitter auf 5 V.
- Die Kamera bleibt aus. Das Board erhält nur WLAN und seinen eigenen Schlüssel.
- Home Assistant darf das Board nicht zusätzlich übernehmen, sonst hat Jarvis keinen Zugriff auf den Sprachkanal.

## Firmware

`firmware/jarvis-mic.yaml` wird vom Workflow **Firmware** mit ESPHome 2026.10.2 gebaut. Das Abbild landet in `jarvis/jarvis/static/firmware/` und wird von Pair und dem Update-Knopf auf der Mikrofonseite benutzt. Nach einer Änderung an der YAML baut der Workflow neu; manuell über *Actions → Firmware → Run workflow*. Das Wakeword auf dem Board ist „Jarvis“. Schon gekoppelte Boards übernehmen es mit dem Update-Knopf, sobald das neue Abbild vorliegt.

## Entwicklung

```bash
cd jarvis
pip install -r requirements-dev.txt
python -m pytest
```

Der Workflow **CI** führt die Tests aus, prüft die Skripte der Oberfläche, baut beide Images und startet sie zum Probelauf.

## Grenzen

- Die Ansage über den Audioserver (Upload und `playuploadedfile` mit dem Miniserver-Token) ist von Loxone nicht offiziell dokumentiert, aber auf Audioserver-Firmware 17.2/17.3 von mehreren Projekten bestätigt.
- Speichern auf den Miniserver per MCP funktioniert nur, wenn Loxone Config dieses Werkzeug anbietet.
- Kameras, E-Mail, Kalender, Einkauf und Türöffnung sind bewusst nicht enthalten.
