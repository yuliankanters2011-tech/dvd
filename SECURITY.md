# Beveiliging

Deze container is bedoeld voor een vertrouwd lokaal netwerk. Het dashboard heeft
geen authenticatie; publiceer poort 8080 niet via internet of een reverse proxy
zonder extra toegangsbeveiliging.

De container is niet `privileged`, maar heeft wel `SYS_ADMIN` nodig om een
bestandssysteem aan te koppelen. Behandel de image en compose-instellingen daarom
als beheerderssoftware. Geef alleen het benodigde `/dev/srX` door.

De converter schrijft alleen naar de twee vaste Jellyfin-bind mounts. Bronnen
komen uit de live schijf of de eigen NAS-bibliotheek; symlinks worden niet
gevolgd. FFmpeg wordt met argumentenlijsten aangestuurd, niet via een shell.
Bestaande MP4's en imports worden niet overschreven. CSS-beveiliging wordt niet
omzeild en er wordt geen decryptiesleutel gevraagd of bewaard.

Meld kwetsbaarheden liever privé via de GitHub-beveiligingsmeldingen van deze
repository dan via een openbaar issue.