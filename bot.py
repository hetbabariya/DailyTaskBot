import os
import json
import logging
from datetime import datetime, timedelta
import schedule
import time
import threading
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv
import pytz
import signal
import sys

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
# Store user preferences in a JSON file
PREFS_FILE = 'user_prefs.json'
# Global bot instance
bot_instance = None
# Global application instance
application = None
# Global scheduler running flag
scheduler_running = False
# Global event loop
loop = None

# Timezone settings
IST = pytz.timezone('Asia/Kolkata')
US_EASTERN = pytz.timezone('US/Eastern')

def load_user_prefs():
    """Load user preferences from JSON file."""
    try:
        if os.path.exists(PREFS_FILE):
            with open(PREFS_FILE, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"Error loading user preferences: {str(e)}")
        return {}

def save_user_prefs(prefs):
    """Save user preferences to JSON file."""
    try:
        with open(PREFS_FILE, 'w') as f:
            json.dump(prefs, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving user preferences: {str(e)}")

def get_user_timezone(user_id: str) -> pytz.timezone:
    """Get user's preferred timezone."""
    prefs = load_user_prefs()
    user_tz = prefs.get(str(user_id), {}).get('timezone', 'IST')
    return IST if user_tz == 'IST' else US_EASTERN

async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set user's preferred timezone."""
    try:
        user_id = str(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(
                'Please specify your timezone:\n'
                '/settimezone IST - for Indian Standard Time (UTC+5:30)\n'
                '/settimezone US - for US Eastern Time (UTC-5)\n\n'
                'Example: /settimezone IST'
            )
            return

        timezone = context.args[0].upper()
        if timezone not in ['IST', 'US']:
            await update.message.reply_text(
                'Invalid timezone. Please use:\n'
                'IST - for Indian Standard Time (UTC+5:30)\n'
                'US - for US Eastern Time (UTC-5)'
            )
            return

        prefs = load_user_prefs()
        if user_id not in prefs:
            prefs[user_id] = {}
        prefs[user_id]['timezone'] = timezone
        save_user_prefs(prefs)

        # Get current time in the new timezone
        user_tz = get_user_timezone(user_id)
        current_time = datetime.now(user_tz).strftime('%H:%M')

        await update.message.reply_text(
            f'✅ Timezone set to {timezone} successfully!\n'
            f'Current time in your timezone: {current_time}\n\n'
            'You can now add tasks using your local time.'
        )
    except Exception as e:
        await update.message.reply_text(f'Error setting timezone: {str(e)}')

def convert_to_utc(time_str: str, user_id: str) -> str:
    """Convert user's local time to UTC."""
    try:
        # Parse the time string
        local_time = datetime.strptime(time_str, '%H:%M')
        # Get user's timezone
        user_tz = get_user_timezone(user_id)
        # Create a datetime object with today's date and the given time
        local_dt = datetime.now(user_tz).replace(
            hour=local_time.hour,
            minute=local_time.minute,
            second=0,
            microsecond=0
        )
        # Convert to UTC
        utc_dt = local_dt.astimezone(pytz.UTC)
        return utc_dt.strftime('%H:%M')
    except Exception as e:
        logger.error(f"Error converting time to UTC: {str(e)}")
        return time_str

def convert_from_utc(time_str: str, user_id: str) -> str:
    """Convert UTC time to user's local time."""
    try:
        # Parse the time string
        utc_time = datetime.strptime(time_str, '%H:%M')
        # Create a datetime object with today's date and the given time
        utc_dt = datetime.now(pytz.UTC).replace(
            hour=utc_time.hour,
            minute=utc_time.minute,
            second=0,
            microsecond=0
        )
        # Convert to user's timezone
        user_tz = get_user_timezone(user_id)
        local_dt = utc_dt.astimezone(user_tz)
        return local_dt.strftime('%H:%M')
    except Exception as e:
        logger.error(f"Error converting time from UTC: {str(e)}")
        return time_str

def get_current_time(user_id: str) -> str:
    """Get current time in user's timezone."""
    user_tz = get_user_timezone(user_id)
    return datetime.now(user_tz).strftime('%H:%M')

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info("Received shutdown signal. Cleaning up...")
    global scheduler_running
    scheduler_running = False
    if application:
        asyncio.run(application.stop())
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

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
    user_id = str(update.effective_user.id)
    
    # Check if user has set timezone
    prefs = load_user_prefs()
    if user_id not in prefs or 'timezone' not in prefs[user_id]:
        # Set default timezone to IST
        if user_id not in prefs:
            prefs[user_id] = {}
        prefs[user_id]['timezone'] = 'IST'
        save_user_prefs(prefs)
        timezone_message = "Your timezone has been set to IST (Indian Standard Time) by default."
    else:
        timezone_message = f"Your current timezone is set to {prefs[user_id]['timezone']}."

    await update.message.reply_text(
        'Welcome to your Daily Task Bot! 🎯\n\n'
        f'{timezone_message}\n\n'
        'Commands:\n'
        '/addtask <time> <task> - Add a new task (e.g., /addtask 14:30 Buy groceries)\n'
        '/addonetime <time> <task> - Add a one-time task\n'
        '/listtasks - List all your tasks\n'
        '/removetask <task_id> - Remove a task\n'
        '/stoptask <task_id> - Stop a recurring task\n'
        '/settimezone <timezone> - Set your timezone (IST or US)\n'
        '/help - Show this help message\n\n'
        'Note: Times should be in 24-hour format (HH:MM)'
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

        # Convert local time to UTC for storage
        utc_time = convert_to_utc(time_str, user_id)
        current_time = get_current_time(user_id)
        user_tz = get_user_timezone(user_id).zone
        
        logger.info(f"Adding task at {time_str} {user_tz} (UTC: {utc_time})")

        # Load existing tasks
        tasks = load_tasks()
        if user_id not in tasks:
            tasks[user_id] = []

        # Add new task
        task_id = len(tasks[user_id]) + 1
        tasks[user_id].append({
            'id': task_id,
            'time': utc_time,  # Store UTC time
            'description': task_description,
            'is_recurring': True,
            'is_active': True
        })

        # Save tasks
        save_tasks(tasks)

        # Schedule the task using UTC time
        schedule_task_reminder(user_id, task_id, utc_time, task_description, is_recurring=True)

        await update.message.reply_text(
            f'Recurring daily task added successfully! 🎉\n'
            f'Task ID: {task_id}\n'
            f'Time ({user_tz}): {time_str}\n'
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

        # Convert local time to UTC for storage
        utc_time = convert_to_utc(time_str, user_id)
        current_time = get_current_time(user_id)
        user_tz = get_user_timezone(user_id).zone
        
        logger.info(f"Adding one-time task at {time_str} {user_tz} (UTC: {utc_time})")

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
            'time': utc_time,  # Store UTC time
            'description': formatted_description,
            'is_recurring': False,
            'is_active': True
        })

        # Save tasks
        save_tasks(tasks)

        # Schedule the task using UTC time
        schedule_task_reminder(user_id, task_id, utc_time, formatted_description, is_recurring=False)

        await update.message.reply_text(
            f'One-time task added successfully! 🎉\n'
            f'Task ID: {task_id}\n'
            f'Time ({user_tz}): {time_str}\n'
            f'Tasks:\n{formatted_description}\n'
            f'Type: One-time'
        )

    except Exception as e:
        await update.message.reply_text(f'Error adding task: {str(e)}')

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tasks for the user."""
    user_id = str(update.effective_user.id)
    tasks = load_tasks()
    user_tz = get_user_timezone(user_id).zone

    if user_id not in tasks or not tasks[user_id]:
        await update.message.reply_text('You have no tasks scheduled.')
        return

    message = 'Your scheduled tasks:\n\n'
    for task in tasks[user_id]:
        task_type = "Daily recurring" if task['is_recurring'] else "One-time"
        status = "Active" if task['is_active'] else "Stopped"
        # Convert UTC time to user's timezone for display
        local_time = convert_from_utc(task['time'], user_id)
        message += (
            f"ID: {task['id']}\n"
            f"Time ({user_tz}): {local_time}\n"
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
        current_time = get_current_time(user_id)
        logger.info(f"Sending reminder for task {task_id} at {current_time} {get_user_timezone(user_id).zone}")
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
    global loop
    job_id = f"task_{user_id}_{task_id}"
    
    # Clear any existing job with the same ID
    schedule.clear(job_id)
    
    # Convert UTC time to user's timezone for logging
    local_time = convert_from_utc(time_str, user_id)
    user_tz = get_user_timezone(user_id).zone
    
    def send_reminder():
        """Function to send reminder using the event loop."""
        try:
            # Get current UTC time
            current_utc = datetime.now(pytz.UTC)
            # Parse the scheduled UTC time
            scheduled_utc = datetime.strptime(time_str, '%H:%M')
            scheduled_utc = current_utc.replace(
                hour=scheduled_utc.hour,
                minute=scheduled_utc.minute,
                second=0,
                microsecond=0
            )
            
            # Check if it's time to send the reminder
            if current_utc.hour == scheduled_utc.hour and current_utc.minute == scheduled_utc.minute:
                logger.info(f"Triggering reminder for task {task_id} at {local_time} {user_tz}")
                asyncio.run_coroutine_threadsafe(
                    send_task_reminder(user_id, task_id, task_description, is_recurring),
                    loop
                )
        except Exception as e:
            logger.error(f"Error in reminder function: {str(e)}")
    
    # Schedule the new job to run every minute
    schedule.every(1).minutes.do(send_reminder).tag(job_id)
    
    logger.info(f"Scheduled task {task_id} for user {user_id} at {local_time} {user_tz} (UTC: {time_str})")

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
    global scheduler_running, loop
    logger.info("Starting scheduler...")
    scheduler_running = True
    
    # Initialize all existing tasks
    initialize_scheduled_tasks()
    
    while scheduler_running:
        try:
            schedule.run_pending()
            time.sleep(1)  # Check every second
        except Exception as e:
            logger.error(f"Error in scheduler: {str(e)}")
            time.sleep(1)

def main():
    """Start the bot."""
    global bot_instance, application, scheduler_running, loop
    
    # Get the token from environment variable
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        logger.error("No TELEGRAM_TOKEN found in environment variables!")
        return

    try:
        # Create the Application
        application = Application.builder().token(token).build()
        bot_instance = application.bot
        loop = asyncio.get_event_loop()

        # Add command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("addtask", add_task))
        application.add_handler(CommandHandler("addonetime", add_one_time_task))
        application.add_handler(CommandHandler("listtasks", list_tasks))
        application.add_handler(CommandHandler("removetask", remove_task))
        application.add_handler(CommandHandler("stoptask", stop_task))
        application.add_handler(CommandHandler("settimezone", set_timezone))

        # Start the scheduler in a separate thread
        scheduler_thread = threading.Thread(target=run_scheduler)
        scheduler_thread.daemon = True
        scheduler_thread.start()

        logger.info("Bot started successfully!")
        
        # Start the bot with proper error handling
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False
        )
    except Exception as e:
        logger.error(f"Error starting bot: {str(e)}")
        scheduler_running = False
        if application:
            asyncio.run(application.stop())
        sys.exit(1)

if __name__ == '__main__':
    main() 