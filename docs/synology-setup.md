# Synology-stappen in gewone taal

## Vooraf

1. Sluit de usb-cd/dvd-speler op de NAS aan. Gebruik bij twijfel een eigen voeding.
2. Installeer **Container Manager** in Package Center.
3. Maak in **Configuratiescherm → Gedeelde map** een map `dvd`.
4. Maak of controleer de Jellyfin-mappen, standaard `/volume1/video/Films` en `/volume1/video/Muziek`.
5. Schakel SSH tijdelijk in via **Configuratiescherm → Terminal & SNMP**.

## Installeren

```bash
git clone https://github.com/yuliankanters2011-tech/dvd.git /volume1/docker/dvd-bridge
cd /volume1/docker/dvd-bridge
sudo bash scripts/prepare-host.sh
sudo docker compose up -d --build
```

Gebruik je een andere Jellyfin-map, bijvoorbeeld `/volume1/media`:

```bash
sudo bash scripts/prepare-host.sh --media-root /volume1/media
```

Het script maakt `Films` en `Muziek` onder die map, zet de eigenaar op je DSM-account en schrijft de paden naar `.env`.

Controleer:

```bash
sudo docker compose ps
sudo docker compose logs --tail=50 dvd
```

Open `http://<NAS-IP>:8080`.

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
- Tijdens kopiëren, ISO-maken of omzetten zijn lade-opdrachten geblokkeerd. Annuleer de conversie eerst als je de schijf wilt verwijderen.

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
sudo docker compose exec dvd python3 -m app.convert --source /srv/dvd/disc --list
sudo docker compose exec dvd python3 -m app.convert \
  --source /srv/dvd/disc --mode video --destination films --name "Vakantie"
```

## Virtuele ISO-schijf in DSM

1. Klik in het dashboard op **ISO maken**.
2. Open File Station en ga naar `dvd/library`.
3. Selecteer het `.iso`-bestand.
4. Kies **Hulpmiddelen → Virtueel station aankoppelen**.
5. Kies een lege doelmap onder een gedeelde map.

Dit is een alleen-lezen virtuele schijf, geen beschrijfbare USB-stick.

## Na een NAS-herstart

De dedicated shared bind-mount kan na een reboot verdwijnen. Maak in DSM een taak:

1. **Configuratiescherm → Taakplanner → Maken → Geplande taak → Door gebruiker gedefinieerd script**.
2. Gebruiker: `root`.
3. Gebeurtenis: **Opstarten**.
4. Script:

```bash
bash /volume1/docker/dvd-bridge/scripts/boot-prepare.sh
cd /volume1/docker/dvd-bridge
/usr/local/bin/docker compose up -d
```

Pas het pad aan als je repository ergens anders staat. Oudere DSM-versies gebruiken `docker-compose` in plaats van `docker compose`.
