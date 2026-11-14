# Beveiliging

Deze container is bedoeld voor een vertrouwd lokaal netwerk. Het dashboard heeft
geen authenticatie; publiceer poort 8080 niet via internet of een reverse proxy
zonder extra toegangsbeveiliging.

De container is niet `privileged`, maar heeft wel `SYS_ADMIN` nodig om een
bestandssysteem aan te koppelen. Behandel de image en compose-instellingen daarom
als beheerderssoftware. Geef alleen het benodigde `/dev/srX` door.

Meld kwetsbaarheden liever privé via de GitHub-beveiligingsmeldingen van deze
repository dan via een openbaar issue.