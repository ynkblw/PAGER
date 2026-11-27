## Neu in dieser Version

- **KI-Agent über ACP (Agent Client Protocol):** PAGER spricht direkt mit Claude Code (später GitHub Copilot CLI). Die Einrichtung erfolgt grafisch unter *Einstellungen → Verbindung zur KI*, mit Einrichtungsprüfung, Anmeldung und Verbindungstest.
- **Neuer Schritttyp „Agent-Fenster“:** zeigt den Output des Agenten, nimmt eigene Nachrichten entgegen und unterstützt Rückfragen (Auswahl mit ↑/↓ und Enter) und Werkzeug-Freigaben. Welche Bestandteile sichtbar sind, legst du im Editor fest.
- **Beispiel-Walkthrough „Agent-Direktkontakt (Test)“** zum Prüfen der Verbindung.
- **Neues Design:** Startseite, farbig hinterlegte Walkthrough-Kacheln und ATLAS-AI-Logo.

## Download

| System | Datei | Hinweis |
|---|---|---|
| Windows 10/11 | `PAGER-…-windows.zip` | **Empfohlen.** Entpacken und `PAGER\PAGER.exe` starten. |
| Windows 10/11 | `PAGER-…-windows.exe` | Einzelne Datei, startet etwas langsamer (entpackt sich bei jedem Start). |
| macOS 12+ (Apple Silicon) | `PAGER-…-macos.zip` | Entpacken und `PAGER.app` in *Programme* ziehen. |

Eine Installation oder Administratorrechte sind nicht nötig.

### Beim ersten Start

Die Programme sind nicht von einem Hersteller signiert. Deshalb warnt das Betriebssystem beim ersten Start:

- **Windows (SmartScreen):** *Weitere Informationen → Trotzdem ausführen*.
- **macOS:** Beim ersten Öffnen meldet macOS, dass PAGER nicht geöffnet werden kann. Wähle dann *Systemeinstellungen → Datenschutz & Sicherheit → Dennoch öffnen*. Alternativ im Terminal: `xattr -dr com.apple.quarantine /Applications/PAGER.app`

### Für den KI-Agenten

PAGER bringt den Agenten nicht mit. Für Claude Code brauchst du die [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/setup) und [Node.js](https://nodejs.org/) (für den ACP-Adapter). Die Einrichtungsprüfung in den Einstellungen zeigt, was fehlt.
