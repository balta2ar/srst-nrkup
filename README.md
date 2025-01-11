# NRKUP - NRK Upload Bot

NRKUP is a Telegram bot service that helps download and process episodes from NRK (Norwegian Broadcasting Corporation).

## Description

This project provides functionality to:
- Download episodes from NRK
- Process media content
- Interact through a Telegram bot interface
- Manage episode metadata and downloads

## Installation (pick one)

```
pipx install srst-nrkup
uv tool install srst-nrkup
```

3. Set up environment variables in a `~/.telegram` file:
```
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_NYHETER_ID=
TELEGRAM_PHONE=
TELEGRAM_ACCESS_TOKEN=
TELEGRAM_CHAT_ID=
```

## Usage

### Running as a Service

The project includes a systemd service file (`nrkup-bot.service`) for running the bot as a system service.
Edit the service file and put into `~/.config/systemd/user/nrkup-bot.service`.
```
