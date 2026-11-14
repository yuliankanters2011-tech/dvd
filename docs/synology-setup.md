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

Gebruik dit ook voor de nieuwe ISO-knoppen. Een herstart alleen haalt geen nieuwe image op.

Versie **1.2.1** herstelt het uitlezen van GNU ddrescue-kaartbestanden. Daardoor kan een al volledig uitgelezen ISO alsnog worden opgeslagen met **ISO hervatten**. Verwijder daarbij niet het verborgen `.partial`-bestand in `dvd/library` en niet de `.mapfile` in de `dvd-state`-volume; die vormen samen het bewaarde deelresultaat.

```bash
sudo docker pull docker.io/ykdigital/dvd-bridge:latest
sudo docker stop dvd-bridge
sudo docker rm dvd-bridge
```

Voer daarna hetzelfde `docker run`-blok opnieuw uit. De `dvd-state`-volume en je NAS-mappen blijven bestaan.

## Bestandssystemen

De container gebruikt de kernel van de NAS. Controleer op de host:

```bash
ls -l /dev/sr*
grep -E 'udf|iso9660' /proc/filesystems
lsmod | grep -E 'cdrom|sr_mod|udf|isofs|nls_utf8'
```

Ontbreekt `/dev/sr0`, dan is dit geen Docker-probleem dat de container kan oplossen. Sommige Synology-modellen missen de optische drivers. Gebruik dan een ondersteunde NAS/Linux-host of een speler die door DSM zelf wordt herkend. Compileer geen willekeurige kernelmodules voor een ander DSM-model.

## Lade sluiten en uitwerpen

- **Lade sluiten** werkt alleen bij spelers met een gemotoriseerde lade. Bij een handmatige slimline-speler duw je de lade voorzichtig dicht.
- **Uitwerpen** koppelt de schijf eerst los. Sluit open bestanden als loskoppelen mislukt.
- Tijdens kopiëren, ISO-maken of omzetten zijn lade-opdrachten geblokkeerd. Gebruik eerst **Annuleren** bij de bewerking die bezig is als je de schijf wilt verwijderen.

## VIDEO_TS naar MP4

1. Plaats een onbeveiligde DVD-Video, of kopieer de schijf eerst naar `dvd/library`.
2. Open het dashboard en kies bij **VIDEO_TS naar MP4** de bron.
3. Kies de titel. “Langste titel” is meestal de hoofdfilm.
4. Kies **Video** en bestemming **Films**, of **Alleen geluid** en bestemming **Muziek**.
5. Vul eventueel een eigen naam in en klik **Omzetten naar MP4**.

De MP4 komt in de gekoppelde Jellyfin-map. Bestaande bestanden worden nooit overschreven. Naast de MP4 staat een `.dvd-bridge.json` marker; verplaats beide als je dezelfde naam opnieuw wilt gebruiken.

`AUDIO_TS` is bij normale dvd's leeg. Het geluid staat in `VIDEO_TS/*.VOB`; kies die bron en “Alleen geluid”. Echte DVD-Audio (AOB), audio-cd's zonder bestandssysteem en CSS-beveiligde dvd's worden niet ondersteund.

CLI-voorbeelden:

```bash
sudo docker exec dvd-bridge python3 -m app.convert --source /srv/dvd/disc --list
sudo docker exec dvd-bridge python3 -m app.convert \
  --source /srv/dvd/disc --mode video --destination films --name "Vakantie"
```

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
