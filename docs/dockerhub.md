# Publiceren naar Docker Hub

Deze repository kan de Docker-image automatisch naar Docker Hub uploaden met GitHub Actions.

## 1. Maak een Docker Hub-repository

Maak op <https://hub.docker.com> een repository met de naam:

```text
dvd-bridge
```

Kies bij voorkeur **Public** als je de image zonder Docker Hub-login op je NAS wilt kunnen downloaden.

## 2. Maak een Docker Hub access token

Ga in Docker Hub naar:

```text
Account Settings → Security → New Access Token
```

Geef de token bijvoorbeeld de naam `github-actions-dvd-bridge` en kies rechten waarmee de image gepusht mag worden, bijvoorbeeld **Read & Write**.

> Deel deze token niet in de chat. Bewaar hem alleen als GitHub Secret.

## 3. Zet twee GitHub Secrets

Ga in deze GitHub-repository naar:

```text
Settings → Secrets and variables → Actions → New repository secret
```

Voeg toe:

| Secret | Waarde |
|---|---|
| `DOCKERHUB_USERNAME` | Je Docker Hub-gebruikersnaam |
| `DOCKERHUB_TOKEN` | Je Docker Hub access token |

## 4. Publiceer

De workflow `.github/workflows/dockerhub.yml` publiceert:

- bij een push naar `main`;
- bij een versietag zoals `v1.0.1`;
- handmatig via **Actions → Publish to Docker Hub → Run workflow**.

De image wordt dan:

```text
docker.io/<DOCKERHUB_USERNAME>/dvd-bridge:latest
```

Voor een versietag krijg je bijvoorbeeld ook:

```text
docker.io/<DOCKERHUB_USERNAME>/dvd-bridge:1.0.1
```

## 5. Installeren op Synology

Gebruik `compose.yaml` en zet in `.env`:

```env
IMAGE_NAME=docker.io/<DOCKERHUB_USERNAME>/dvd-bridge
IMAGE_TAG=latest
```

Daarna:

```bash
sudo bash scripts/prepare-host.sh
sudo docker compose pull
sudo docker compose up -d
```

Of upload `compose.yaml` als project in Synology Container Manager en stel dezelfde environmentwaarden in. De hostvoorbereiding blijft nodig omdat de NAS-map `rshared` moet zijn en `/dev/sr0` beschikbaar moet zijn.