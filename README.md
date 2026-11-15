# DVD Bridge voor Synology

Een Docker-container die een **usb-cd/dvd-speler op je Synology NAS** uitleest en de bestanden in een **gewone gedeelde NAS-map** zet. Je kunt de map openen in File Station, via SMB op Windows/macOS, de bestanden naar de NAS kopiëren, of onbeveiligde DVD-video omzetten naar **MP4 voor Jellyfin**.

> **Belangrijk:** dit maakt geen echte beschrijfbare USB-stick van de speler. Een cd/dvd blijft alleen-lezen. De container maakt de inhoud zichtbaar als normale bestanden en kan optioneel een kopie, ISO-bestand of MP4 op de NAS bewaren.

## Wat werkt er?

- Gegevens-cd's en dvd's met **ISO 9660** of **UDF**.
- Automatisch aankoppelen na het inwerpen van een schijf.
- Live, alleen-lezen map: `/volume1/dvd/disc`.
- Optioneel automatisch kopiëren naar `/volume1/dvd/library`.
- Webdashboard met status, kopieerknop, ISO-knop, annuleerknoppen, lade sluiten en uitwerpen. Vanaf **1.2.9** met een duidelijke CSS-blokkade (uitgeschakelde knoppen + reden), minder geflikker, betere laad-/leeg-/foutmeldingen, een snellere bronnenlijst en een mobiele, toegankelijke layout.
- Slaapstand voor de usb-speler met handmatige knop en instelbare inactiviteitstimer, plus een slider om de draaisnelheid aan te vragen. Beide zijn best-effort: de usb-voeding blijft aan en niet elke speler/usb-brug ondersteunt de commando's.
- ISO-bestanden die je in DSM File Station als virtuele schijf kunt aankoppelen.
- Licht beschadigde ISO's worden toch opgeslagen; bij zware schade verschijnt **Toch doorgaan**. Een geannuleerde ISO kan later worden hervat.
- **VIDEO_TS naar MP4**: kies een titel uit de dvd of een losse `.ts`/`.vob`/`.mpg`/`.mpeg` uit de NAS-bibliotheek. Overgeslagen bronnen krijgen een zichtbare reden en een waarschuwing in de containerlog.
- Twee vaste Jellyfin-bestemmingen: **Films** (H.264 + AAC) en **Muziek** (alleen AAC-geluid in MP4).
- Voortgang, annuleren, hervatten via idempotente markers en nooit bestaande bestanden overschrijven.
- Geen `privileged: true`; alleen `SYS_ADMIN`, één apparaat en AppArmor-unconfined voor mounten.

## Wat werkt niet automatisch?

- Een cd/dvd-speler echt als beschrijfbare USB-stick of harde schijf voordoen.
- Audio-cd's rippen naar MP3.
- Lege cd's/dvd's beschrijven.
- CSS/beveiligde film-dvd's omzetten. Vanaf 1.2.8 verschijnt vroeg: **“Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video.”** De container bevat geen decryptie en omzeilt geen beveiliging.
- Echte DVD-Audio (AUDIO_TS/AOB) en audio-cd's zonder bestandssysteem. Het geluid van een normale DVD-Video staat in `VIDEO_TS/*.VOB`; kies daarvoor “Alleen geluid”.
- DVD-menu's, meerdere camerahoeken tegelijk, shuffle-titels en het kiezen van een andere audiotrack dan de eerste. Bij een hoekblok wordt alleen hoek 1 bewaard.
- Synology-modellen/DSM-versies zonder `cdrom`/`sr_mod`-ondersteuning. Docker kan geen ontbrekende NAS-kerneldriver repareren.

Synology ondersteunt extern optische media niet op alle modellen. Controleer daarom eerst of de NAS `/dev/sr0` aanmaakt. Zie de Synology-communitymelding over usb-dvd-spelers: <https://community.synology.com/enu/forum/1/post/153115>.

## Snel starten (SSH, zonder Compose)

De aanbevolen route voor deze NAS gebruikt alleen `docker run`, met hostpoort **8085**. De volledige kant-en-klare commando's staan in [docs/synology-setup.md](docs/synology-setup.md#installeren-via-ssh-zonder-compose). Je hebt daar geen compose-bestand voor nodig.

```text
http://<NAS-IP>:8085
```

De oudere Compose-route hieronder blijft bestaan voor wie Container Manager-projecten gebruikt, maar is niet nodig voor de SSH-installatie.

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

### 4. Bereid de host voor en start (optionele Compose-route)

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
2. Kies de titel. Standaard wordt de langste ondersteunde titel gekozen; titel 0 betekent “langste titel”. Kies “Alle ondersteunde titels in één MP4” (titel -1) om alle ondersteunde titels achter elkaar in één bestand te bewaren. Een extra alles-afspelen-titel die dezelfde videobestanden hergebruikt, wordt niet dubbel toegevoegd; niet-ondersteunde titels worden overgeslagen.
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
  --source /srv/dvd/disc --title -1 --mode video --destination films --name "Hele dvd"
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
| `ISO_MAX_MISSING_PERCENT` | `0.5` | Percentage onleesbare bytes dat automatisch in een ISO mag blijven (0–100). Meer schade vraagt **Toch doorgaan** |
| `FILMS_PATH` | `/volume1/video/Films` | Hostmap voor Jellyfin-films |
| `MUSIC_PATH` | `/volume1/video/Muziek` | Hostmap voor Jellyfin-muziek |
| `CONVERT_THREADS` | `2` | FFmpeg-threads (1–16); lager houdt de NAS responsiever |
| `CONVERT_CRF` | `20` | H.264-kwaliteit (1–51); lager = beter en groter |
| `CONVERT_PRESET` | `veryfast` | `ultrafast` t/m `medium`; sneller = minder CPU, groter bestand |
| `CONVERT_AUDIO_BITRATE` | `192` | AAC-kbps (64–512) |
| `SLEEP_IDLE_SECONDS` | `600` | Automatische slaapstand na zoveel seconden zonder opdracht/activiteit; `0` = uit, anders 60–86400 |
| `DRIVE_SPEED` | `0` | Draaisnelheid aanvragen: `0` = standaard/maximum, 1–24 Linux cdrom-eenheden (1× ≈ 177 kB/s) |
| `IMAGE_NAME` | `ghcr.io/yuliankanters2011-tech/dvd` | Image-repository, bijvoorbeeld `docker.io/<user>/dvd-bridge` |
| `IMAGE_TAG` | `latest` | Imagetag |

De containerpaden `FILMS_DIR` en `MUSIC_DIR` staan vast op `/volume1/video/Films` en `/volume1/video/Muziek`; de hostpaden hierboven worden daarnaartoe gemount. `prepare-host.sh --media-root` past de hostpaden aan.

Voorbeeld automatisch kopiëren:

```bash
sudo bash scripts/prepare-host.sh --copy --auto-eject
sudo docker compose up -d --build
```

Handmatig kopiëren of een ISO maken kan ook via het dashboard. Een ISO kun je daarna in File Station kiezen via **Hulpmiddelen → Virtueel station aankoppelen**. Synology documenteert dit voor ISO 9660/UDF-images: <https://kb.synology.com/en-global/DSM/help/FileStation/mountiso?version=7>.

### ISO van een beschadigde schijf

GNU ddrescue leest de schijf in een verborgen `.partial`-bestand met een mapfile:

1. **Kleine schade** (standaard ≤ 0,5% onleesbaar): de ISO wordt automatisch opgeslagen. De naastgelegen `.dvd-bridge.json` vermeldt `partial: true`, het aantal onleesbare bytes en het percentage.
2. **Zware schade**: het dashboard toont **Toch doorgaan**. Kies dit alleen als je bewust een beschadigde ISO wilt bewaren; bestanden kunnen onvolledig zijn. De ISO en marker vertellen dat het resultaat beschadigd is.
3. **Annuleren**: het deelresultaat en de mapfile blijven bewaard. Plaats dezelfde schijf terug en klik **ISO hervatten** om verder te gaan waar het stopte.

De tolerantie stel je in met `ISO_MAX_MISSING_PERCENT` (0–100, standaard 0.5). Bij een losse `docker run`-installatie voeg je `-e ISO_MAX_MISSING_PERCENT=0.5` toe. Bestaande ISO's en markers worden nooit overschreven.

Versie 1.2.1 herkent de statusregel in GNU ddrescue-kaartbestanden correct. Oudere versies konden daardoor een volledig uitgelezen ISO onterecht weigeren met “Het ddrescue-kaartbestand bevat ongeldige getallen”. Update de container en klik op **ISO hervatten** met dezelfde schijf; verwijder het verborgen `.partial`-bestand en de mapfile niet.

Versie **1.2.2** herstelt de titelkeuze bij gewone DVD-Video. Een celcommando's-vlag in de VMG-tabel betekent niet dat een titel shuffle gebruikt. De converter controleert nu de echte afspeelmodus per PGC; normale afleveringen en opeenvolgende multi-PGC-titels blijven kiesbaar. Alleen niet-sequentiële PGC's (random/shuffle) worden geweigerd. Zie je bij een oudere versie `titels: 9` maar `ondersteund: 0`, werk dan bij naar 1.2.2 en klik **Bronnen verversen**.

Versie **1.2.3** herstelt een regressie in 1.2.2: de PGC-afspeelmodus werd op byte `0xA1` gelezen in plaats van `0xA3` (`pg_playback_mode` in de schijfindeling). Daardoor konden gewone titels onterecht als shuffle worden geweigerd of kon een complete VIDEO_TS-import als bron verdwijnen. De indexlezer volgt nu beter de tolerantie van libdvdread voor dubbele celadressen, een te groot opgegeven aantal cellen, gedeelde PGC-startposities en afgekapte PTT-titeltabellen. Een onleesbare enkele titel blokkeert andere leesbare titels niet meer. Als een bron toch wordt overgeslagen, tonen het dashboard en `/api/media` nu de reden en het pad, en schrijft de container een waarschuwing op standaard logniveau. Werk bij naar **1.2.3** en klik **Bronnen verversen**; de bestaande import hoeft niet opnieuw gekopieerd.

Versie **1.2.4** herstelt de DVD-tijdsduur na de diagnose “De DVD-index bevat een ongeldige tijdsduur”. In `dvd_time_t` zijn de twee hoogste bits van het vierde byte de framerate (`01` = 25 fps, `11` = 29,97 fps) en de lage zes bits het BCD-frameaantal. De lezer telde de framerate-bits eerder mee als frames en kende `01` niet als 25 fps, waardoor geldige PAL-titels zoals de KNIGHT_RIDER-import werden geweigerd. De foutmelding bevat nu ook de gelezen tijdbytes. Werk bij naar **1.2.4** en klik **Bronnen verversen**; de bestaande import hoeft niet opnieuw gekopieerd. Kies bij voorkeur de NAS-bibliotheekbron als de live schijf I/O-fouten geeft.

Versie **1.2.5** herstelt de celadrestabel (C_ADT) na de diagnose “De DVD-index mist het adres van een videocel”. Het veld `nr_of_vobs` is het aantal VOB-id's, niet het aantal 12-byte celadressen; één VOB kan veel cellen bevatten. De lezer volgt nu libdvdread en loopt door de volledige tabel op basis van `last_byte`, waardoor meerdelige afleveringen in één VTS niet meer halverwege worden geweigerd. De melding noemt nu ook de ontbrekende vob/cel-id. Werk bij naar **1.2.5** en klik **Bronnen verversen**; de bestaande import hoeft niet opnieuw gekopieerd.

Versie **1.2.6** voegt de keuze “Alle ondersteunde titels in één MP4” toe bij **Titel**. De converter zet de ondersteunde titels achter elkaar in één MP4 en herkent een alles-afspelen-titel die dezelfde VOB-cellen hergebruikt, zodat afleveringen niet dubbel worden opgeslagen. Losse afleveringen, video/audio-modi, Films/Muziek, voortgang, annuleren en niet-overschrijven blijven werken. De marker vermeldt de opgenomen titelnummers. Werk bij naar **1.2.6** en klik **Bronnen verversen**; de bestaande import hoeft niet opnieuw gekopieerd.

Versie **1.2.8** herkent CSS-beveiligde dvd's via de copyright-informatie van de speler, een begrensde controle van VOB-sectorkoppen en de kernel-sense `0x5 / 6F / 03`. Het dashboard en de logs tonen: **“Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video.”** Kopiëren, ISO maken en omzetten van zo'n live schijf worden vroeg geweigerd; onbeveiligde DVD-Video, gegevensschijven en losse videobestanden blijven werken.

Versie **1.2.9** verbetert het dashboard zelf: een duidelijke CSS-blokkade met uitgeschakelde knoppen en een reden per knop, minder geflikker doordat status en bronnenlijst alleen gewijzigde teksten/keuzelijsten bijwerken, betere laad-, leeg- en foutmeldingen, een snellere bronnenlijst met debounce en caching, en een mobiele layout met zichtbare focus en labels. **ISO maken ontgrendelt of ontsleutelt niets**; een CSS-beveiligde dvd wordt ook als ISO geweigerd. De CSS-detectie en alle bestaande import-, ISO-, marker- en slaapfuncties blijven ongewijzigd. Werk bij naar **1.2.9** en klik **Bronnen verversen**.

Versie **1.2.7** voegt **Slaapstand & draaisnelheid** toe: een handmatige slaap/wakker-knop, een instelbare automatische slaaptimer en een slider om de draaisnelheid van de usb-speler aan te vragen. Tijdens slaapstand pauzeert DVD Bridge de spelercontroles; de usb-voeding blijft aan en het stopcommando is best-effort. De bestaande import, ISO's en markers blijven ongewijzigd.

## Slaapstand en draaisnelheid

Het dashboardblok **Slaapstand & draaisnelheid** regelt de usb-speler zelf, niet de NAS of container:

- **Slaapstand** koppelt de schijf eerst los en stuurt daarna het Linux `CDROMSTOP`-commando. De schijf hoort dan te stoppen met draaien, maar de usb-voeding blijft aan. Sommige spelers/usb-bruggen negeren het commando; DVD Bridge belooft daarom geen echte power-off.
- **Automatisch slapen** gebeurt na de ingestelde tijd zonder opdrachten, conversies of schijfwissels. Standaard is dat 10 minuten (`SLEEP_IDLE_SECONDS=600`); `0` schakelt het uit. De keuze wordt bewaard in `dvd-state/drive-settings.json`.
- Tijdens slaapstand worden status-, media- en mountcontroles van de speler gepauzeerd, zodat het dashboard de schijf niet elke paar seconden weer laat opstarten. **Wakker worden**, uitwerpen, lade sluiten of een opdracht op de live schijf hervat het gebruik. Conversies vanuit de NAS-bibliotheek kunnen gewoon doorgaan terwijl de speler slaapt.
- De **slider** vraagt een draaisnelheid aan via `CDROM_SELECT_SPEED`: `0` is standaard/maximum, `1`–`24` zijn Linux cdrom-snelheidseenheden. Het echte toerental hangt af van de speler, usb-brug en schijf; dit is geen gemeten RPM en niet de FFmpeg-conversiesnelheid. Een niet-ondersteunde aanvraag geeft een waarschuwing zonder de rest van het dashboard te blokkeren.
- Slaapstand en snelheidswerking worden geblokkeerd tijdens kopiëren, ISO-maken of omzetten. Annuleer eerst als je de speler wilt laten slapen.

De container krijgt het apparaat nog steeds als `/dev/sr0:r` en gebruikt alleen read-only cdrom-ioctls. Er is geen `privileged`, host-USB-toegang of extra kernelmodule nodig. Echte usb-voeding uitschakelen kan vanuit deze container niet.

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
| Speler blijft draaien in slaapstand | De speler/usb-brug negeert `CDROMSTOP`. DVD Bridge pauzeert wel zijn eigen controles; echte usb-voeding uitschakelen kan niet vanuit de container |
| Snelheidsslider geeft waarschuwing | De speler/usb-brug ondersteunt `CDROM_SELECT_SPEED` niet, of de aanvraag mislukt. De rest van het dashboard blijft werken |
| `FFmpeg ontbreekt` | Oude image; pull/build de nieuwe image met het `ffmpeg`-pakket |
| `ddrescue-kaartbestand bevat ongeldige getallen` | Versie 1.2.0 of ouder; update naar 1.2.1 en klik **ISO hervatten** zonder het `.partial`-bestand of de mapfile te verwijderen |
| Conversie meldt “bestaat al” | Er staat al een MP4 of marker; kies een andere naam of verplaats beide |
| Bron toont wel titels maar `ondersteund: 0` | Versie 1.2.1 of ouder kan gewone titels met celcommando's onterecht als shuffle weigeren; update naar 1.2.2. Versie 1.2.2 leest de PGC-modus op de verkeerde byte; update naar 1.2.3. Versie 1.2.3 kan geldige PAL-tijden afkeuren; update naar 1.2.4. Versie 1.2.4 kan meerdelige C_ADT-tabellen te kort uitlezen; update naar 1.2.5 |
| Geen omzetbare bronnen, maar de import is compleet | Update naar 1.2.6 en klik **Bronnen verversen**. Lees anders de nieuwe melding onder de bronkeuze, `/api/media` (`skipped_sources`) of `docker logs dvd-bridge` voor de exacte reden |
| `ongeldige tijdsduur` | Versie 1.2.3 of ouder telt de framerate-bits mee als frames; update naar 1.2.4. De melding bevat nu de tijdbytes |
| `mist het adres van een videocel` | Versie 1.2.4 of ouder gebruikt `nr_of_vobs` als aantal celadressen; update naar 1.2.5. De melding bevat nu de vob/cel-id |
| Live schijf geeft `I/O error`, NAS-kopie niet | Gebruik de NAS-bibliotheekbron. De live schijf/speler heeft leesfouten; controleer schijf, USB-kabel en voeding |
| `Sense Key 0x5, ASC=0x6f ASCQ=0x3` of “CSS-beveiligd” | De speler weigert een versleutelde sector zonder authenticatie. Dit is CSS, geen gewone beschadiging. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video en omzeilt geen beveiliging |
| Conversie meldt beschadigd/beveiligd | Bron is mogelijk beschadigd of geen ondersteunde DVD-Video; bij CSS verschijnt vanaf 1.2.8 de expliciete CSS-melding |
| Dashboard niet bereikbaar | Container draait niet, poort is bezet, of firewall blokkeert poort 8080 |

## Beveiliging en gedrag

- Het dashboard heeft **geen wachtwoord** en is bedoeld voor een vertrouwd lokaal netwerk. Zet poort 8080 niet open naar internet.
- De container krijgt alleen `/dev/sr0` read-only doorgegeven, plus `SYS_ADMIN` om bestandssystemen aan te koppelen. Er is geen host-netwerk en geen host-root mount.
- De originele schijf wordt altijd met `ro,nosuid,nodev,noexec` aangekoppeld.
- Kopiëren schrijft eerst naar een verborgen staging-map en publiceert die pas na voltooiing. Bestaande imports worden niet overschreven.
- Symlinks, apparaatbestanden en FIFO's op de schijf worden overgeslagen bij kopiëren.
- ISO-maken gebruikt GNU ddrescue. Een beetje schade (standaard maximaal 0,5%) wordt automatisch opgeslagen en gemarkeerd in de naastgelegen `.json`. Bij zware schade kies je bewust **Toch doorgaan**; de ISO blijft dan gemarkeerd als beschadigd. Annuleren bewaart het verborgen deelresultaat plus mapfile om later te hervatten. Bestaande ISO's worden nooit overschreven.
- De converter accepteert alleen bron-id's uit de vaste bronnenlijst en schrijft alleen naar de twee gekoppelde Jellyfin-mappen. FFmpeg wordt zonder shell aangestuurd.
- MP4-publicatie gebeurt met een harde link die faalt als het doel al bestaat. De marker wordt apart gepubliceerd; bij een markerfout wordt de nieuwe MP4 teruggedraaid.
- CSS wordt niet omzeild. Vanaf 1.2.8 verschijnt vroeg **“Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video.”** en worden kopiëren, ISO maken en omzetten van de beveiligde live schijf geblokkeerd. DVD-Audio, menu's en shuffle-titels geven een uitleg in plaats van een stille mislukking.

## Ontwikkelen en testen

```bash
python3 -m pip install -r requirements-dev.txt
ruff check app tests
ruff format --check app tests
python3 -m pytest
shellcheck scripts/*.sh
docker compose config --quiet
# Dashboard (DOM-tests met jsdom, geen browser of schijf nodig):
npm ci --ignore-scripts
npm run check
npm test
```

CI draait unit-tests, lint, Compose-validatie, een dashboard-smoke-test, echte FFmpeg-conversies op synthetische TS/VOB/VIDEO_TS-media, echte ddrescue-tests op een tijdelijk schijfbestand en echte read-only ISO9660/UDF-loopmounttests. Een aparte `dashboard`-job draait de jsdom-DOM-tests (`tests/web/`) die het renderen, de CSS-blokkade, polling/debounce, foutafhandeling en de mobiele/toegankelijke markup controleren; de image-job wacht op beide. De smoke-test controleert ook een echt ddrescue 1.29-kaartbestand in de containerimage. De hardware-specifieke Synology-stappen moeten uiteindelijk op je eigen NAS worden gecontroleerd.

Lokaal worden de FFmpeg-conversietests overgeslagen als `ffmpeg`/`ffprobe` niet in `PATH` staan. Installeer ze bijvoorbeeld met `sudo apt-get install ffmpeg` of `brew install ffmpeg`. De ddrescue-integratietests worden overgeslagen als `ddrescue` ontbreekt; installeer dat met `sudo apt-get install gddrescue` of `brew install ddrescue`.

## Bronnen / inspiratie

- Optische Linux-apparaten gebruiken `/dev/srX`: <https://github.com/jlesage/docker-makemkv#access-to-optical-drives>
- Docker bind-propagation: <https://docs.docker.com/engine/storage/bind-mounts/#configure-bind-propagation>
- Synology virtuele ISO-drives: <https://kb.synology.com/en-global/DSM/help/FileStation/mountiso?version=7>

Licentie: MIT, zie `LICENSE`.