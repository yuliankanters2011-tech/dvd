# DVD Bridge voor Synology

Een Docker-container die een **usb-cd/dvd-speler op je Synology NAS** uitleest en de bestanden in een **gewone gedeelde NAS-map** zet. Je kunt de map openen in File Station, via SMB op Windows/macOS, de bestanden naar de NAS kopiëren, of onbeveiligde DVD-video omzetten naar **MP4 voor Jellyfin**.

> **Belangrijk:** dit maakt geen echte beschrijfbare USB-stick van de speler. Een cd/dvd blijft alleen-lezen. De container maakt de inhoud zichtbaar als normale bestanden en kan optioneel een kopie, ISO-bestand of MP4 op de NAS bewaren.

## Wat werkt er?

- Gegevens-cd's en dvd's met **ISO 9660** of **UDF**.
- Automatisch aankoppelen na het inwerpen van een schijf.
- Live, alleen-lezen map: `/volume1/dvd/disc`.
- Optioneel automatisch kopiëren naar `/volume1/dvd/library`.
- Webdashboard met status, kopieerknop, ISO-knop, lade sluiten en uitwerpen.
- ISO-bestanden die je in DSM File Station als virtuele schijf kunt aankoppelen.
- **VIDEO_TS naar MP4**: kies een titel uit de dvd of een losse `.ts`/`.vob`/`.mpg`/`.mpeg` uit de NAS-bibliotheek.
- Twee vaste Jellyfin-bestemmingen: **Films** (H.264 + AAC) en **Muziek** (alleen AAC-geluid in MP4).
- Voortgang, annuleren, hervatten via idempotente markers en nooit bestaande bestanden overschrijven.
- Geen `privileged: true`; alleen `SYS_ADMIN`, één apparaat en AppArmor-unconfined voor mounten.

## Wat werkt niet automatisch?

- Een cd/dvd-speler echt als beschrijfbare USB-stick of harde schijf voordoen.
- Audio-cd's rippen naar MP3.
- Lege cd's/dvd's beschrijven.
- CSS/beveiligde film-dvd's omzetten. De ruwe `.VOB`-bestanden kunnen soms wel zichtbaar zijn, maar de container bevat geen decryptie en omzeilt geen beveiliging.
- Echte DVD-Audio (AUDIO_TS/AOB) en audio-cd's zonder bestandssysteem. Het geluid van een normale DVD-Video staat in `VIDEO_TS/*.VOB`; kies daarvoor “Alleen geluid”.
- DVD-menu's, meerdere camerahoeken tegelijk, shuffle-titels en het kiezen van een andere audiotrack dan de eerste. Bij een hoekblok wordt alleen hoek 1 bewaard.
- Synology-modellen/DSM-versies zonder `cdrom`/`sr_mod`-ondersteuning. Docker kan geen ontbrekende NAS-kerneldriver repareren.

Synology ondersteunt extern optische media niet op alle modellen. Controleer daarom eerst of de NAS `/dev/sr0` aanmaakt. Zie de Synology-communitymelding over usb-dvd-spelers: <https://community.synology.com/enu/forum/1/post/153115>.

## Snel starten (SSH)

### 1. Maak de gedeelde map in DSM

Ga naar **Configuratiescherm → Gedeelde map → Maken** en maak een map met de naam `dvd`, bijvoorbeeld op `/volume1/dvd`. Geef je DSM-account lees-/schrijfrechten.

### 2. Zet deze repository op de NAS

Bijvoorbeeld in `/volume1/docker/dvd-bridge`:

```bash
git clone https://github.com/yuliankanters2011-tech/dvd.git /volume1/docker/dvd-bridge
cd /volume1/docker/dvd-bridge
```

Heb je geen git? Download de ZIP via GitHub en pak die uit in File Station.

### 3. Sluit de speler aan en controleer

```bash
ls -l /dev/sr*
```

Je moet iets als `/dev/sr0` zien. Zie je niets, controleer dan de usb-kabel/voeding en `dmesg | tail -50`.

### 4. Bereid de host voor en start

```bash
sudo bash scripts/prepare-host.sh
sudo docker compose up -d --build
```

Het script:

- detecteert `/dev/srX`;
- probeert aanwezige kernelmodules te laden;
- maakt `disc` en `library` in de gedeelde map;
- maakt alleen die map `rshared` voor mount-propagation;
- maakt `Films` en `Muziek` onder de Jellyfin-map (standaard `/volume1/video`);
- schrijft `.env` met je DSM UID/GID en de converterinstellingen.

Gebruik een andere Jellyfin-map met:

```bash
sudo bash scripts/prepare-host.sh --media-root /volume1/media
```

Open daarna:

```text
http://<NAS-IP>:8080
```

De live schijf staat in:

```text
/volume1/dvd/disc
```

### 5. Gebruik als normale netwerkmap

- **DSM:** File Station → `dvd/disc`.
- **Windows:** Verkenner → `\\<NAS-IP>\dvd\disc` → optie *Netwerkverbinding maken* als je een stationsletter wilt.
- **macOS:** Finder → Ga → Verbind met server → `smb://<NAS-IP>/dvd/disc`.

De stationsletter/network drive is een normale SMB-mapweergave, geen USB-blokapparaat.

## DVD-video naar MP4 voor Jellyfin

Open het dashboard en gebruik het blok **VIDEO_TS naar MP4**:

1. Kies een bron: de geplaatste dvd (`Schijf`) of een eerdere import/losse videobestand uit `dvd/library` (`NAS`).
2. Kies de titel. Standaard wordt de langste ondersteunde titel gekozen; titel 0 betekent “langste titel”.
3. Kies **Video** voor een film in `Films`, of **Alleen geluid** voor een AAC-MP4 in `Muziek`.
4. Kies de bestemming en eventueel een eigen bestandsnaam.
5. Klik **Omzetten naar MP4**. Laat de NAS en de usb-speler aangesloten tot de conversie klaar is.

De uitvoer staat standaard in:

```text
/volume1/video/Films   # H.264-video + AAC-geluid, MP4
/volume1/video/Muziek  # alleen AAC-geluid, MP4
```

Voeg deze mappen in Jellyfin toe als bibliotheek. De container schrijft eerst een verborgen `.partial.mp4`, controleert het resultaat met `ffprobe` en publiceert het daarna zonder bestaande bestanden te overschrijven. Naast elke MP4 staat een `.dvd-bridge.json` marker. Dezelfde bron/titel/instellingen opnieuw aanbieden is een no-op; bij een andere bron of gewijzigde instellingen moet je een andere naam kiezen of het bestaande bestand plus marker verplaatsen.

CLI in de container:

```bash
sudo docker compose exec dvd python3 -m app.convert --source /srv/dvd/disc --list
sudo docker compose exec dvd python3 -m app.convert \
  --source /srv/dvd/disc --title 0 --mode video --destination films --name "Mijn film"
sudo docker compose exec dvd python3 -m app.convert \
  --source /srv/dvd/library/IMPORT/VIDEO_TS --mode audio --destination music
```

`--output-dir` is een expliciete doelmap en mag niet samen met `--destination` worden gebruikt. Annuleren kan via het dashboard; SIGINT/SIGTERM breekt de CLI netjes af. Onvoltooide verborgen partiële bestanden worden bij een normale annulering verwijderd.

## Container Manager zonder SSH-bouw

Als de image al op GHCR of Docker Hub staat, kun je in Container Manager een project maken met `compose.yaml` en een `.env` naast het projectbestand. De hostvoorbereiding met `prepare-host.sh` blijft nog steeds nodig; een Compose-project kan de host-mount niet zelf `shared` maken.

Standaardimage (GitHub Container Registry):

```text
ghcr.io/yuliankanters2011-tech/dvd:latest
```

Voor Docker Hub stel je in `.env` in:

```env
IMAGE_NAME=docker.io/<jouw-dockerhub-gebruikersnaam>/dvd-bridge
IMAGE_TAG=latest
```

De GitHub Actions-workflow bouwt voor `linux/amd64` en `linux/arm64` en publiceert bij pushes naar de standaardbranch/tags. Op een featurebranch kun je altijd lokaal bouwen met `--build`.

### Publiceren naar Docker Hub

Zie [docs/dockerhub.md](docs/dockerhub.md). Kort gezegd:

1. Maak een Docker Hub-repository `dvd-bridge`.
2. Maak een Docker Hub access token.
3. Zet in GitHub bij **Settings → Secrets and variables → Actions** twee repository secrets: `DOCKERHUB_USERNAME` en `DOCKERHUB_TOKEN`.
4. De workflow **Publish to Docker Hub** publiceert daarna naar `docker.io/<gebruikersnaam>/dvd-bridge:latest` bij een push naar `main`, een versietag, of handmatig via **Actions → Run workflow**.

## Instellingen

`scripts/prepare-host.sh` schrijft `.env`. Belangrijkste opties:

| Variabele | Standaard | Betekenis |
|---|---:|---|
| `SHARE_PATH` | `/volume1/dvd` | Gedeelde DSM-map |
| `DEVICE` | auto | Hostapparaat, bijvoorbeeld `/dev/sr0` |
| `WEB_PORT` | `8080` | Dashboardpoort |
| `WEB_BIND` | `0.0.0.0` | Luisteradres; gebruik `127.0.0.1` als je alleen lokaal wilt |
| `PUID`/`PGID` | DSM-account | Eigenaar van gekopieerde bestanden |
| `POLL_SECONDS` | `5` | Interval voor schijfdetectie |
| `COPY_FILES` | `false` | Nieuwe schijven automatisch kopiëren naar `library` |
| `AUTO_EJECT` | `false` | Uitwerpen na geslaagde automatische kopie of live-dvd-conversie |
| `FILMS_PATH` | `/volume1/video/Films` | Hostmap voor Jellyfin-films |
| `MUSIC_PATH` | `/volume1/video/Muziek` | Hostmap voor Jellyfin-muziek |
| `CONVERT_THREADS` | `2` | FFmpeg-threads (1–16); lager houdt de NAS responsiever |
| `CONVERT_CRF` | `20` | H.264-kwaliteit (1–51); lager = beter en groter |
| `CONVERT_PRESET` | `veryfast` | `ultrafast` t/m `medium`; sneller = minder CPU, groter bestand |
| `CONVERT_AUDIO_BITRATE` | `192` | AAC-kbps (64–512) |
| `IMAGE_NAME` | `ghcr.io/yuliankanters2011-tech/dvd` | Image-repository, bijvoorbeeld `docker.io/<user>/dvd-bridge` |
| `IMAGE_TAG` | `latest` | Imagetag |

De containerpaden `FILMS_DIR` en `MUSIC_DIR` staan vast op `/volume1/video/Films` en `/volume1/video/Muziek`; de hostpaden hierboven worden daarnaartoe gemount. `prepare-host.sh --media-root` past de hostpaden aan.

Voorbeeld automatisch kopiëren:

```bash
sudo bash scripts/prepare-host.sh --copy --auto-eject
sudo docker compose up -d --build
```

Handmatig kopiëren of een ISO maken kan ook via het dashboard. Een ISO kun je daarna in File Station kiezen via **Hulpmiddelen → Virtueel station aankoppelen**. Synology documenteert dit voor ISO 9660/UDF-images: <https://kb.synology.com/en-global/DSM/help/FileStation/mountiso?version=7>.

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

## Problemen oplossen

```bash
sudo bash scripts/doctor.sh
sudo docker compose logs --tail=100 dvd
```

| Probleem | Waarschijnlijke oorzaak |
|---|---|
| Geen `/dev/sr0` | Speler niet herkend, te weinig usb-voeding, of DSM mist `cdrom`/`sr_mod` |
| `mount: permission denied` | Container Manager/DSM blokkeert mounten; controleer `SYS_ADMIN`, AppArmor en DSM-versie |
| NAS-map blijft leeg | `prepare-host.sh` is niet uitgevoerd, of `SHARE_PATH` wijst naar een andere map |
| `wrong fs type` / `invalid argument` | Kernel mist `udf`, `isofs` of `nls_utf8`, of de schijf is geen gegevensschijf |
| Uitwerpen mislukt | Er staan nog bestanden open; sluit ze of gebruik de fysieke knop |
| Lade sluiten doet niets | Slimline/manual speler zonder motor; duw de lade voorzichtig dicht |
| `FFmpeg ontbreekt` | Oude image; pull/build de nieuwe image met het `ffmpeg`-pakket |
| Conversie meldt “bestaat al” | Er staat al een MP4 of marker; kies een andere naam of verplaats beide |
| Conversie meldt beschadigd/beveiligd | Bron is mogelijk CSS-versleuteld, beschadigd of geen ondersteunde DVD-Video |
| Dashboard niet bereikbaar | Container draait niet, poort is bezet, of firewall blokkeert poort 8080 |

## Beveiliging en gedrag

- Het dashboard heeft **geen wachtwoord** en is bedoeld voor een vertrouwd lokaal netwerk. Zet poort 8080 niet open naar internet.
- De container krijgt alleen `/dev/sr0` read-only doorgegeven, plus `SYS_ADMIN` om bestandssystemen aan te koppelen. Er is geen host-netwerk en geen host-root mount.
- De originele schijf wordt altijd met `ro,nosuid,nodev,noexec` aangekoppeld.
- Kopiëren schrijft eerst naar een verborgen staging-map en publiceert die pas na voltooiing. Bestaande imports worden niet overschreven.
- Symlinks, apparaatbestanden en FIFO's op de schijf worden overgeslagen bij kopiëren.
- ISO-maken gebruikt GNU ddrescue en bewaart geen partieel resultaat als er onleesbare blokken zijn.
- De converter accepteert alleen bron-id's uit de vaste bronnenlijst en schrijft alleen naar de twee gekoppelde Jellyfin-mappen. FFmpeg wordt zonder shell aangestuurd.
- MP4-publicatie gebeurt met een harde link die faalt als het doel al bestaat. De marker wordt apart gepubliceerd; bij een markerfout wordt de nieuwe MP4 teruggedraaid.
- CSS wordt niet omzeild. DVD-Audio, menu's en shuffle-titels geven een uitleg in plaats van een stille mislukking.

## Ontwikkelen en testen

```bash
python3 -m pip install -r requirements-dev.txt
ruff check app tests
ruff format --check app tests
python3 -m pytest
shellcheck scripts/*.sh
docker compose config --quiet
```

CI draait unit-tests, lint, Compose-validatie, een dashboard-smoke-test, echte FFmpeg-conversies op synthetische TS/VOB/VIDEO_TS-media en echte read-only ISO9660/UDF-loopmounttests. De hardware-specifieke Synology-stappen moeten uiteindelijk op je eigen NAS worden gecontroleerd.

Lokaal worden de FFmpeg-conversietests overgeslagen als `ffmpeg`/`ffprobe` niet in `PATH` staan. Installeer ze bijvoorbeeld met `sudo apt-get install ffmpeg` of `brew install ffmpeg`.

## Bronnen / inspiratie

- Optische Linux-apparaten gebruiken `/dev/srX`: <https://github.com/jlesage/docker-makemkv#access-to-optical-drives>
- Docker bind-propagation: <https://docs.docker.com/engine/storage/bind-mounts/#configure-bind-propagation>
- Synology virtuele ISO-drives: <https://kb.synology.com/en-global/DSM/help/FileStation/mountiso?version=7>

Licentie: MIT, zie `LICENSE`.