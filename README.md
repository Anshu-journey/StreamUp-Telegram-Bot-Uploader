# StreamUP Upload Telegram Bot

A Telegram bot that uploads files to StreamUP.cc. This bot can handle files larger than the standard 20MB Telegram bot limit using Pyrogram's chunked downloading.

## Features

- Upload any file sent to the bot directly to StreamUP
- Supports documents, videos, photos, and audio files
- Real-time progress tracking for downloads
- Handles files larger than 20MB

## Setup

1. **Clone this repository**

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up your environment variables**
   
   Copy the example .env file and fill in your details:
   ```bash
   cp .env.example .env
   ```
   
   You'll need to get your API_ID and API_HASH from https://my.telegram.org/apps and create a bot using @BotFather to get your BOT_TOKEN.

4. **Run the bot**
   ```bash
   python streamup_upload_bot.py
   ```

## Usage

1. Start the bot by sending `/start`
2. Send any file to the bot (document, video, photo, audio)
3. The bot will download the file, upload it to StreamUP, and send you the link

## Requirements

- Python 3.7+
- Pyrogram
- TgCrypto (for faster performance)
- requests
- python-dotenv

## Note

The StreamUP API key is already included in the script. If you need to change it, update the `STREAMUP_API_KEY` variable in `streamup_upload_bot.py`. 