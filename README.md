# Daily Task Telegram Bot

A Telegram bot that helps you manage your daily tasks with scheduling capabilities. The bot sends you a daily reminder of your tasks at 7 AM.

## Features

- Add tasks with specific times
- List all your tasks
- Remove tasks
- Daily task reminders at 7 AM
- Persistent storage of tasks

## Setup

1. Create a new Telegram bot:
   - Open Telegram and search for "@BotFather"
   - Send `/newbot` and follow the instructions
   - Copy the API token provided by BotFather

2. Create a `.env` file in the project root and add your Telegram bot token:
   ```
   TELEGRAM_TOKEN=your_bot_token_here
   ```

3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the bot:
   ```bash
   python bot.py
   ```

## Deployment on Render.com

1. Create a new account on [Render.com](https://render.com)
2. Create a new Web Service
3. Connect your GitHub repository
4. Configure the service:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python bot.py`
   - Add environment variable: `TELEGRAM_TOKEN` with your bot token

## Bot Commands

- `/start` - Start the bot and see available commands
- `/help` - Show help message
- `/addtask <time> <task>` - Add a new task (e.g., `/addtask 14:30 Buy groceries`)
- `/listtasks` - List all your tasks
- `/removetask <task_id>` - Remove a task by its ID

## Example Usage

1. Start the bot: `/start`
2. Add a task: `/addtask 14:30 Buy groceries`
3. List tasks: `/listtasks`
4. Remove a task: `/removetask 1`

The bot will automatically send you a daily reminder of all your tasks at 7 AM. 