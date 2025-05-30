import os
import json
import logging
from datetime import datetime
import schedule
import time
import threading
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)

logger = logging.getLogger(__name__)

# Store tasks in a JSON file
TASKS_FILE = 'tasks.json'
# Global bot instance
bot_instance = None

def load_tasks():
    """Load tasks from JSON file with error handling."""
    try:
        if os.path.exists(TASKS_FILE):
            with open(TASKS_FILE, 'r') as f:
                content = f.read()
                if not content.strip():  # If file is empty
                    return {}
                return json.loads(content)
        return {}
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error loading tasks: {str(e)}")
        return {}

def save_tasks(tasks):
    """Save tasks to JSON file with error handling."""
    try:
        with open(TASKS_FILE, 'w') as f:
            json.dump(tasks, f, indent=4)
    except IOError as e:
        logger.error(f"Error saving tasks: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        'Welcome to your Daily Task Bot! 🎯\n\n'
        'Commands:\n'
        '/addtask <time> <task> - Add a new task (e.g., /addtask 14:30 Buy groceries)\n'
        '/addonetime <time> <task> - Add a one-time task\n'
        '/listtasks - List all your tasks\n'
        '/removetask <task_id> - Remove a task\n'
        '/stoptask <task_id> - Stop a recurring task\n'
        '/help - Show this help message'
    )

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new recurring daily task."""
    try:
        # Get user ID
        user_id = str(update.effective_user.id)
        
        # Parse command arguments
        if len(context.args) < 2:
            await update.message.reply_text('Please provide time and task description.\nExample: /addtask 14:30 Buy groceries')
            return

        time_str = context.args[0]
        task_description = ' '.join(context.args[1:])

        # Validate time format
        try:
            datetime.strptime(time_str, '%H:%M')
        except ValueError:
            await update.message.reply_text('Invalid time format. Please use HH:MM format (e.g., 14:30)')
            return

        # Load existing tasks
        tasks = load_tasks()
        if user_id not in tasks:
            tasks[user_id] = []

        # Add new task
        task_id = len(tasks[user_id]) + 1
        tasks[user_id].append({
            'id': task_id,
            'time': time_str,
            'description': task_description,
            'is_recurring': True,
            'is_active': True
        })

        # Save tasks
        save_tasks(tasks)

        # Schedule the task
        schedule_task_reminder(user_id, task_id, time_str, task_description, is_recurring=True)

        await update.message.reply_text(
            f'Recurring daily task added successfully! 🎉\n'
            f'Task ID: {task_id}\n'
            f'Time: {time_str}\n'
            f'Description: {task_description}\n'
            f'Type: Daily recurring'
        )

    except Exception as e:
        await update.message.reply_text(f'Error adding task: {str(e)}')

async def add_one_time_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a one-time task."""
    try:
        # Get user ID
        user_id = str(update.effective_user.id)
        
        # Parse command arguments
        if len(context.args) < 2:
            await update.message.reply_text('Please provide time and task description.\nExample: /addonetime 14:30 Buy groceries')
            return

        time_str = context.args[0]
        task_description = ' '.join(context.args[1:])

        # Validate time format
        try:
            datetime.strptime(time_str, '%H:%M')
        except ValueError:
            await update.message.reply_text('Invalid time format. Please use HH:MM format (e.g., 14:30)')
            return

        # Process task description to handle multiple tasks
        tasks_list = [task.strip() for task in task_description.split('-') if task.strip()]
        formatted_description = '\n'.join([f"• {task}" for task in tasks_list])

        # Load existing tasks
        tasks = load_tasks()
        if user_id not in tasks:
            tasks[user_id] = []

        # Add new task
        task_id = len(tasks[user_id]) + 1
        tasks[user_id].append({
            'id': task_id,
            'time': time_str,
            'description': formatted_description,
            'is_recurring': False,
            'is_active': True
        })

        # Save tasks
        save_tasks(tasks)

        # Schedule the task
        schedule_task_reminder(user_id, task_id, time_str, formatted_description, is_recurring=False)

        await update.message.reply_text(
            f'One-time task added successfully! 🎉\n'
            f'Task ID: {task_id}\n'
            f'Time: {time_str}\n'
            f'Tasks:\n{formatted_description}\n'
            f'Type: One-time'
        )

    except Exception as e:
        await update.message.reply_text(f'Error adding task: {str(e)}')

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tasks for the user."""
    user_id = str(update.effective_user.id)
    tasks = load_tasks()

    if user_id not in tasks or not tasks[user_id]:
        await update.message.reply_text('You have no tasks scheduled.')
        return

    message = 'Your scheduled tasks:\n\n'
    for task in tasks[user_id]:
        task_type = "Daily recurring" if task['is_recurring'] else "One-time"
        status = "Active" if task['is_active'] else "Stopped"
        message += (
            f"ID: {task['id']}\n"
            f"Time: {task['time']}\n"
            f"Task: {task['description']}\n"
            f"Type: {task_type}\n"
            f"Status: {status}\n\n"
        )

    await update.message.reply_text(message)

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a task by ID."""
    try:
        if not context.args:
            await update.message.reply_text('Please provide a task ID to remove.')
            return

        task_id = int(context.args[0])
        user_id = str(update.effective_user.id)
        tasks = load_tasks()

        if user_id not in tasks:
            await update.message.reply_text('You have no tasks scheduled.')
            return

        # Find and remove the task
        tasks[user_id] = [task for task in tasks[user_id] if task['id'] != task_id]
        save_tasks(tasks)

        # Clear the scheduled job for this task
        job_id = f"task_{user_id}_{task_id}"
        schedule.clear(job_id)

        await update.message.reply_text(f'Task {task_id} has been removed.')

    except ValueError:
        await update.message.reply_text('Please provide a valid task ID (number).')
    except Exception as e:
        await update.message.reply_text(f'Error removing task: {str(e)}')

async def stop_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop a recurring task."""
    try:
        if not context.args:
            await update.message.reply_text('Please provide a task ID to stop.')
            return

        task_id = int(context.args[0])
        user_id = str(update.effective_user.id)
        tasks = load_tasks()

        if user_id not in tasks:
            await update.message.reply_text('You have no tasks scheduled.')
            return

        # Find the task
        task = next((t for t in tasks[user_id] if t['id'] == task_id), None)
        if not task:
            await update.message.reply_text('Task not found.')
            return

        if not task['is_recurring']:
            await update.message.reply_text('This is a one-time task and cannot be stopped.')
            return

        # Update task status
        task['is_active'] = False
        save_tasks(tasks)

        # Clear the scheduled job for this task
        job_id = f"task_{user_id}_{task_id}"
        schedule.clear(job_id)

        await update.message.reply_text(f'Recurring task {task_id} has been stopped.')

    except ValueError:
        await update.message.reply_text('Please provide a valid task ID (number).')
    except Exception as e:
        await update.message.reply_text(f'Error stopping task: {str(e)}')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    await start(update, context)

async def send_task_reminder(user_id: str, task_id: int, task_description: str, is_recurring: bool):
    """Send a reminder for a specific task."""
    global bot_instance
    try:
        message = f'⏰ Task Reminder!\n\n{task_description}'
        await bot_instance.send_message(chat_id=user_id, text=message)

        # If it's a one-time task, remove it after sending
        if not is_recurring:
            tasks = load_tasks()
            if user_id in tasks:
                tasks[user_id] = [task for task in tasks[user_id] if task['id'] != task_id]
                save_tasks(tasks)

    except Exception as e:
        logger.error(f"Error sending reminder to user {user_id}: {str(e)}")

def schedule_task_reminder(user_id: str, task_id: int, time_str: str, task_description: str, is_recurring: bool):
    """Schedule a reminder for a specific task."""
    job_id = f"task_{user_id}_{task_id}"
    
    # Clear any existing job with the same ID
    schedule.clear(job_id)
    
    # Schedule the new job
    if is_recurring:
        schedule.every().day.at(time_str).do(
            lambda: asyncio.run(send_task_reminder(user_id, task_id, task_description, True))
        ).tag(job_id)
    else:
        schedule.every().day.at(time_str).do(
            lambda: asyncio.run(send_task_reminder(user_id, task_id, task_description, False))
        ).tag(job_id)

def initialize_scheduled_tasks():
    """Initialize all scheduled tasks from the tasks file."""
    tasks = load_tasks()
    for user_id, user_tasks in tasks.items():
        for task in user_tasks:
            if task['is_active']:
                schedule_task_reminder(
                    user_id,
                    task['id'],
                    task['time'],
                    task['description'],
                    task['is_recurring']
                )

def run_scheduler():
    """Run the scheduler in a separate thread."""
    logger.info("Starting scheduler...")
    # Initialize all existing tasks
    initialize_scheduled_tasks()
    
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except Exception as e:
            logger.error(f"Error in scheduler: {str(e)}")
            time.sleep(60)  # Wait before retrying

def main():
    """Start the bot."""
    global bot_instance
    
    # Get the token from environment variable
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        logger.error("No TELEGRAM_TOKEN found in environment variables!")
        return

    try:
        # Create the Application
        application = Application.builder().token(token).build()
        bot_instance = application.bot

        # Add command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("addtask", add_task))
        application.add_handler(CommandHandler("addonetime", add_one_time_task))
        application.add_handler(CommandHandler("listtasks", list_tasks))
        application.add_handler(CommandHandler("removetask", remove_task))
        application.add_handler(CommandHandler("stoptask", stop_task))

        # Start the scheduler in a separate thread
        scheduler_thread = threading.Thread(target=run_scheduler)
        scheduler_thread.daemon = True
        scheduler_thread.start()

        logger.info("Bot started successfully!")
        
        # Start the bot
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Error starting bot: {str(e)}")

if __name__ == '__main__':
    main() 