# Synology-stappen in gewone taal

## Vooraf

1. Sluit de usb-cd/dvd-speler op de NAS aan. Gebruik bij twijfel een eigen voeding.
2. Installeer **Container Manager** in Package Center.
3. Maak in **Configuratiescherm → Gedeelde map** een map `dvd`.
4. Maak of controleer de Jellyfin-mappen, standaard `/volume1/video/Films` en `/volume1/video/Muziek`.
5. Schakel SSH tijdelijk in via **Configuratiescherm → Terminal & SNMP**.

## Installeren via SSH, zonder Compose

Deze route gebruikt alleen `docker run`. Je hebt geen compose-bestand nodig. Vul bij `PUID`/`PGID` de UID/GID van je DSM-account in (zichtbaar met `id -u` en `id -g`).

```bash
sudo mkdir -p /volume1/dvd/disc /volume1/dvd/library /volume1/video/Films /volume1/video/Muziek
sudo chown "$(id -u):$(id -g)" /volume1/dvd/disc /volume1/dvd/library /volume1/video/Films /volume1/video/Muziek
sudo chmod 0755 /volume1/dvd/disc /volume1/dvd/library /volume1/video/Films /volume1/video/Muziek

# Maak alleen de dvd-map shared, niet het hele volume.
if ! awk '$5 == "/volume1/dvd" {found=1} END {exit !found}' /proc/self/mountinfo; then
  sudo mount --bind /volume1/dvd /volume1/dvd
fi
sudo mount --make-rshared /volume1/dvd

sudo docker pull docker.io/ykdigital/dvd-bridge:latest
sudo docker volume create dvd-state
sudo docker run -d --name dvd-bridge --restart unless-stopped --init \
  --stop-timeout 30 --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
  --device /dev/sr0:/dev/sr0:r -p 8085:8080 \
  -e DEVICE=/dev/sr0 -e SHARE_DIR=/srv/dvd -e STATE_DIR=/var/lib/dvd \
  -e POLL_SECONDS=5 -e WEB_PORT=8080 -e PUID=1024 -e PGID=100 \
  -e COPY_FILES=false -e AUTO_EJECT=false -e ISO_MAX_MISSING_PERCENT=0.5 \
  -e FILMS_DIR=/volume1/video/Films -e MUSIC_DIR=/volume1/video/Muziek \
  -e CONVERT_THREADS=2 -e CONVERT_CRF=20 -e CONVERT_PRESET=veryfast \
  -e CONVERT_AUDIO_BITRATE=192 \
  -e SLEEP_IDLE_SECONDS=600 -e DRIVE_SPEED=0 \
  --mount type=bind,source=/volume1/dvd,target=/srv/dvd,bind-propagation=rshared \
  --mount type=volume,source=dvd-state,target=/var/lib/dvd \
  --mount type=bind,source=/volume1/video/Films,target=/volume1/video/Films \
  --mount type=bind,source=/volume1/video/Muziek,target=/volume1/video/Muziek \
  --log-opt max-size=5m --log-opt max-file=3 \
  docker.io/ykdigital/dvd-bridge:latest
```

Controleer:

```bash
sudo docker logs --tail=80 dvd-bridge
curl http://127.0.0.1:8085/healthz
```

Open `http://<NAS-IP>:8085`. De containerpoort blijft 8080; alleen de NAS-poort is 8085.

## Updaten zonder Compose

Een herstart alleen haalt geen nieuwe image op. Onderstaand blok werkt de bestaande `dvd-bridge`-container bij naar **1.2.8**, zonder Compose en met behoud van de `dvd-state`-volume, ISO's, markers en NAS-mappen. Hostpoort blijft **8085**. Het blok stopt en vervangt de container; voer het alleen uit als je die korte onderbreking wilt.

Versie **1.2.8** herkent CSS-beveiligde dvd's en toont vroeg: **“Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video.”** Kopiëren, ISO maken en omzetten van zo'n live schijf worden geweigerd voordat een lange leespoging begint. De melding staat ook in `/api/status`, `/api/media` (`skipped_sources`) en de containerlogs. Er wordt geen CSS-ontsleuteling toegevoegd.

Versie **1.2.7** voegt **Slaapstand & draaisnelheid** toe: een handmatige slaap/wakker-knop, een instelbare automatische slaaptimer en een slider om de draaisnelheid van de usb-speler aan te vragen. Tijdens slaapstand pauzeert DVD Bridge de spelercontroles; de usb-voeding blijft aan en het stopcommando is best-effort. De bestaande import, ISO's en markers blijven ongewijzigd.

Versie **1.2.6** voegt bij **Titel** de keuze “Alle ondersteunde titels in één MP4” toe. De titels worden achter elkaar in één MP4 bewaard; een extra alles-afspelen-titel die dezelfde videobestanden hergebruikt, wordt niet dubbel toegevoegd. Losse afleveringen blijven gewoon kiesbaar. De bestaande import hoeft niet opnieuw gekopieerd. Kies na het updaten bij voorkeur de NAS-bibliotheekbron als de live schijf `I/O error` geeft. Versie **1.2.5** herstelde de celadrestabel, zodat meerdelige titels in één VTS niet meer onterecht geweigerd worden. Versie **1.2.1** herstelde al het uitlezen van GNU ddrescue-kaartbestanden; verwijder bij **ISO hervatten** nooit het verborgen `.partial`-bestand of de `.mapfile`.

Voer dit volledige blok uit via SSH:

```bash
set -e
sudo docker pull docker.io/ykdigital/dvd-bridge:latest
if sudo docker container inspect dvd-bridge >/dev/null 2>&1; then
  sudo docker stop dvd-bridge
  sudo docker rm dvd-bridge
fi
if ! awk '$5 == "/volume1/dvd" {found=1} END {exit !found}' /proc/self/mountinfo; then
  sudo mount --bind /volume1/dvd /volume1/dvd
fi
sudo mount --make-rshared /volume1/dvd
sudo docker volume create dvd-state
sudo docker run -d --name dvd-bridge --restart unless-stopped --init \
  --stop-timeout 30 --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
  --device /dev/sr0:/dev/sr0:r -p 8085:8080 \
  -e DEVICE=/dev/sr0 -e SHARE_DIR=/srv/dvd -e STATE_DIR=/var/lib/dvd \
  -e POLL_SECONDS=5 -e WEB_PORT=8080 -e PUID=1024 -e PGID=100 \
  -e COPY_FILES=false -e AUTO_EJECT=false -e ISO_MAX_MISSING_PERCENT=0.5 \
  -e FILMS_DIR=/volume1/video/Films -e MUSIC_DIR=/volume1/video/Muziek \
  -e CONVERT_THREADS=2 -e CONVERT_CRF=20 -e CONVERT_PRESET=veryfast \
  -e CONVERT_AUDIO_BITRATE=192 \
  -e SLEEP_IDLE_SECONDS=600 -e DRIVE_SPEED=0 \
  --mount type=bind,source=/volume1/dvd,target=/srv/dvd,bind-propagation=rshared \
  --mount type=volume,source=dvd-state,target=/var/lib/dvd \
  --mount type=bind,source=/volume1/video/Films,target=/volume1/video/Films \
  --mount type=bind,source=/volume1/video/Muziek,target=/volume1/video/Muziek \
  --log-opt max-size=5m --log-opt max-file=3 \
  docker.io/ykdigital/dvd-bridge:latest
poging=0
while [ "$poging" -lt 10 ]; do
  if curl --fail --silent http://127.0.0.1:8085/healthz >/dev/null; then break; fi
  poging=$((poging + 1))
  sleep 2
done
curl --fail --silent http://127.0.0.1:8085/healthz
printf '\n'
sudo docker exec dvd-bridge python3 -c 'from app import __version__; print("DVD Bridge versie:", __version__)'
```

Open daarna het dashboard op poort **8085** en klik **Bronnen verversen**. Onderaan hoort `v1.2.8` te staan.

## Bestandssystemen

De container gebruikt de kernel van de NAS. Controleer op de host:

```bash
ls -l /dev/sr*
grep -E 'udf|iso9660' /proc/filesystems
lsmod | grep -E 'cdrom|sr_mod|udf|isofs|nls_utf8'
```

Ontbreekt `/dev/sr0`, dan is dit geen Docker-probleem dat de container kan oplossen. Sommige Synology-modellen missen de optische drivers. Gebruik dan een ondersteunde NAS/Linux-host of een speler die door DSM zelf wordt herkend. Compileer geen willekeurige kernelmodules voor een ander DSM-model.

## Slaapstand en draaisnelheid

Het dashboardblok **Slaapstand & draaisnelheid** werkt vanaf **1.2.7**:

- **Slaapstand** koppelt de schijf los en stuurt een stopcommando naar de usb-speler. De usb-voeding blijft aan; sommige spelers blijven toch draaien. Dit is geen echte power-off en geen NAS/container-shutdown.
- **Automatisch slapen** staat standaard op 10 minuten zonder opdracht/activiteit. Kies een andere tijd of **Uit** in het dashboard, of zet `-e SLEEP_IDLE_SECONDS=0` bij `docker run`. De dashboardkeuze wordt bewaard in `dvd-state/drive-settings.json`.
- Tijdens slaapstand controleert DVD Bridge de speler niet meer, zodat hij niet meteen weer opstart. Klik **Wakker worden**, uitwerpen, lade sluiten of start een opdracht op de live schijf om verder te gaan. Een conversie uit de NAS-bibliotheek hoeft de speler niet wakker te maken.
- De **slider** vraagt een draaisnelheid aan: 0 = standaard/maximum, 1–24 Linux cdrom-eenheden. Het echte toerental is afhankelijk van de speler/usb-brug; niet elke speler ondersteunt dit. Dit verandert niet de MP4-conversiesnelheid.
- Tijdens kopiëren, ISO-maken of omzetten zijn slaapstand en snelheidswijzigingen geblokkeerd. Annuleer eerst als je de speler wilt laten slapen.

De device-toegang blijft `/dev/sr0:r`; er is geen privileged mode of host-USB-toegang nodig.

## Lade sluiten en uitwerpen

- **Lade sluiten** werkt alleen bij spelers met een gemotoriseerde lade. Bij een handmatige slimline-speler duw je de lade voorzichtig dicht.
- **Uitwerpen** koppelt de schijf eerst los. Sluit open bestanden als loskoppelen mislukt.
- Tijdens kopiëren, ISO-maken of omzetten zijn lade-opdrachten geblokkeerd. Gebruik eerst **Annuleren** bij de bewerking die bezig is als je de schijf wilt verwijderen.

## VIDEO_TS naar MP4

1. Plaats een onbeveiligde DVD-Video, of kopieer de schijf eerst naar `dvd/library`.
2. Open het dashboard en kies bij **VIDEO_TS naar MP4** de bron.
3. Kies de titel. “Langste titel” is meestal de hoofdfilm; bij een serie kies je de gewenste aflevering. Kies “Alle ondersteunde titels in één MP4” om alle ondersteunde titels achter elkaar in één bestand te bewaren. Herhaalde videobestanden van een alles-afspelen-titel worden niet dubbel toegevoegd; niet-ondersteunde titels worden overgeslagen.
4. Kies **Video** en bestemming **Films**, of **Alleen geluid** en bestemming **Muziek**.
5. Vul eventueel een eigen naam in en klik **Omzetten naar MP4**.

Vanaf versie **1.2.5** blijven gewone titels met celcommando's en opeenvolgende multi-PGC-titels kiesbaar, wordt de PGC-afspeelmodus op de juiste schijfbyte gelezen, worden DVD-tijden met 25 fps (`01`) of 29,97 fps (`11`) correct gedecodeerd en wordt de volledige C_ADT-celadrestabel gelezen. Alleen echte niet-sequentiële weergave (random/shuffle) krijgt de melding dat die titel niet wordt ondersteund. Een onleesbare enkele titel blokkeert andere leesbare titels niet meer. Zie je geen ondersteunde titels, klik dan **Bronnen verversen** en controleer onderaan dat de versie `v1.2.7` of nieuwer is.

Als een complete import toch ontbreekt, toont het dashboard onder de bronkeuze de reden en het pad. Dezelfde informatie staat in `/api/media` onder `skipped_sources` en als waarschuwing in `sudo docker logs --tail=100 dvd-bridge`. Je hoeft niet opnieuw te kopiëren: update eerst de container en ververs de bronnen.

De MP4 komt in de gekoppelde Jellyfin-map. Bestaande bestanden worden nooit overschreven. Naast de MP4 staat een `.dvd-bridge.json` marker; verplaats beide als je dezelfde naam opnieuw wilt gebruiken. Bij “Alle ondersteunde titels” krijgt de naam de toevoeging `- Alle titels` en vermeldt de marker welke titels zijn opgenomen. Tijdens het omzetten worden verborgen deelbestanden gebruikt; bij annuleren of een fout worden die en het partiële eindbestand opgeruimd.

`AUDIO_TS` is bij normale dvd's leeg. Het geluid staat in `VIDEO_TS/*.VOB`; kies die bron en “Alleen geluid”. Echte DVD-Audio (AOB), audio-cd's zonder bestandssysteem en CSS-beveiligde dvd's worden niet ondersteund.

CLI-voorbeelden:

```bash
sudo docker exec dvd-bridge python3 -m app.convert --source /srv/dvd/disc --list
sudo docker exec dvd-bridge python3 -m app.convert \
  --source /srv/dvd/disc --mode video --destination films --name "Vakantie"
sudo docker exec dvd-bridge python3 -m app.convert \
  --source /srv/dvd/disc --title -1 --mode video --destination films --name "Hele dvd"
```

## CSS-beveiligde dvd's

Vanaf **1.2.8** verschijnt bij een CSS-beveiligde live schijf vroeg:

> Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video.

Dezelfde melding staat in `/api/status`, onder `/api/media` bij `skipped_sources` en in `sudo docker logs --tail=100 dvd-bridge`. Kopiëren, ISO maken en omzetten van die live schijf worden geweigerd voordat een lange leespoging begint. Uitwerpen, lade sluiten en slaapstand blijven beschikbaar.

Herken je in `dmesg` of de logs `Sense Key 0x5` met `ASC=0x6f ASCQ=0x3`, dan weigert de speler een versleutelde sector zonder authenticatie. Dat is CSS, geen gewone krasser of usb-voedingsfout. DVD Bridge vraagt geen sleutels aan en omzeilt de beveiliging niet. Gebruik een onbeveiligde DVD-Video of een gegevensschijf; onbeveiligde imports en losse videobestanden blijven gewoon werken.

## Virtuele ISO-schijf in DSM

1. Klik in het dashboard op **ISO maken**.
2. Open File Station en ga naar `dvd/library`.
3. Selecteer het `.iso`-bestand.
4. Kies **Hulpmiddelen → Virtueel station aankoppelen**.
5. Kies een lege doelmap onder een gedeelde map.

Dit is een alleen-lezen virtuele schijf, geen beschrijfbare USB-stick.

### Beschadigde schijf of annuleren

- Een klein beetje onleesbare data (standaard maximaal **0,5%**) mag automatisch in de ISO blijven. De ISO wordt opgeslagen en de naastgelegen `.dvd-bridge.json` vermeldt hoeveel bytes missen.
- Is de schijf **sterk beschadigd**, dan verschijnt **Toch doorgaan**. Daarmee bewaar je bewust een beschadigde ISO. Bestanden in die ISO kunnen onvolledig zijn.
- **ISO annuleren** stopt het maken. Het verborgen deelresultaat en de mapfile blijven bewaard; later verschijnt **ISO hervatten** voor dezelfde schijf.
- **Kopiëren annuleren** stopt het kopiëren en verwijdert alleen de onvoltooide importmap. Eerder voltooide imports blijven staan.
- De automatische tolerantie stel je in met `ISO_MAX_MISSING_PERCENT` (0–100). Bij een losse `docker run`-installatie voeg je `-e ISO_MAX_MISSING_PERCENT=0.5` toe en maak je de container opnieuw.

## Na een NAS-herstart

De dedicated shared bind-mount kan na een reboot verdwijnen. Maak in DSM een taak:

1. **Configuratiescherm → Taakplanner → Maken → Geplande taak → Door gebruiker gedefinieerd script**.
2. Gebruiker: `root`.
3. Gebeurtenis: **Opstarten**.
4. Script (geen Compose nodig):

```bash
if ! awk '$5 == "/volume1/dvd" {found=1} END {exit !found}' /proc/self/mountinfo; then
  mount --bind /volume1/dvd /volume1/dvd
fi
mount --make-rshared /volume1/dvd
/usr/local/bin/docker restart dvd-bridge
```

Als `docker restart` meldt dat de bind-mount niet bestaat, stop/verwijder de container en voer het `docker run`-blok uit de installatie opnieuw uit. De `dvd-state`-volume blijft bewaard.
