# Synology-stappen in gewone taal

## Vooraf

1. Sluit de usb-cd/dvd-speler op de NAS aan. Gebruik bij twijfel een eigen voeding.
2. Installeer **Container Manager** in Package Center.
3. Maak in **Configuratiescherm → Gedeelde map** een map `dvd`.
4. Schakel SSH tijdelijk in via **Configuratiescherm → Terminal & SNMP**.

## Installeren

```bash
git clone https://github.com/yuliankanters2011-tech/dvd.git /volume1/docker/dvd-bridge
cd /volume1/docker/dvd-bridge
sudo bash scripts/prepare-host.sh
sudo docker compose up -d --build
```

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

Ontbreekt `/dev/sr0`, dan is dit geen Docker-probleem dat de container kan oplossen. Sommige Synology-modellen missen de optische drivers. Gebruik dan een ondersteunde NAS/Linux-host of een speler die door DSM zelf wordt herkend.

## Virtuele ISO-schijf in DSM

1. Klik in het dashboard op **ISO maken**.
2. Open File Station en ga naar `dvd/library`.
3. Selecteer het `.iso`-bestand.
4. Kies **Hulpmiddelen → Virtueel station aankoppelen**.
5. Kies een lege doelmap onder een gedeelde map.

Dit is een alleen-lezen virtuele schijf, geen beschrijfbare USB-stick.