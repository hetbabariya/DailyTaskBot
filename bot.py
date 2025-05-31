import os
import json
import logging
from datetime import datetime, timedelta
import schedule
import time
import threading
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv
import pytz
import signal
import sys
from aiohttp import web
from telegram.error import Conflict, NetworkError, TimedOut
import backoff

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
# Global web app
web_app = None

# Timezone settings
IST = pytz.timezone('Asia/Kolkata')
US_EASTERN = pytz.timezone('US/Eastern')

# Add these constants at the top with other constants
TASK_STATUS = {
    'NOT_STARTED': '⏳ Not Started',
    'PENDING': '🔄 Pending',
    'DONE': '✅ Done'
}

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
                tasks = json.loads(content)
                # Ensure each task has a status
                for user_tasks in tasks.values():
                    for task in user_tasks:
                        if 'status' not in task:
                            task['status'] = 'NOT_STARTED'
                return tasks
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

async def show_tasks_page(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, page: int = 0, filter_type: str = 'all'):
    """Show a page of tasks with pagination."""
    tasks = load_tasks()
    user_tz = get_user_timezone(user_id).zone
    
    if user_id not in tasks or not tasks[user_id]:
        if update.callback_query:
            await update.callback_query.edit_message_text('No tasks found.')
        else:
            await update.message.reply_text('No tasks found.')
        return
    
    # Filter tasks based on status
    if filter_type == 'all':
        filtered_tasks = tasks[user_id]
    else:
        status_map = {
            'done': 'DONE',
            'not_started': 'NOT_STARTED',
            'pending': 'PENDING'
        }
        target_status = status_map.get(filter_type)
        logger.info(f"Filtering tasks for status: {target_status}")
        
        filtered_tasks = []
        for task in tasks[user_id]:
            task_status = task.get('status', 'NOT_STARTED')
            if task_status == target_status:
                filtered_tasks.append(task)
                logger.info(f"Task {task['id']} matches filter {target_status}")
    
    logger.info(f"Found {len(filtered_tasks)} tasks matching filter {filter_type}")
    
    if not filtered_tasks:
        message = f'No {filter_type.replace("_", " ")} tasks found.'
        if update.callback_query:
            await update.callback_query.edit_message_text(message)
        else:
            await update.message.reply_text(message)
        return
    
    # Calculate pagination - show only 1 task per page
    TASKS_PER_PAGE = 1
    total_pages = len(filtered_tasks)
    page = max(0, min(page, total_pages - 1))  # Ensure page is within bounds
    
    # Get the current task
    current_task = filtered_tasks[page]
    
    # Build message with detailed task information
    task_type = "Daily recurring" if current_task['is_recurring'] else "One-time"
    status = TASK_STATUS.get(current_task.get('status', 'NOT_STARTED'), '⏳ Not Started')
    local_time = convert_from_utc(current_task['time'], user_id)
    
    message = (
        f"📋 Task Details (Page {page + 1} of {total_pages})\n\n"
        f"Task ID: {current_task['id']}\n"
        f"Time ({user_tz}): {local_time}\n"
        f"Type: {task_type}\n"
        f"Status: {status}\n"
        f"Description:\n{current_task['description']}\n"
    )
    
    # Create keyboard with status buttons
    keyboard = []
    
    # Add status buttons for the current task
    status_buttons = []
    for status, label in TASK_STATUS.items():
        display_label = f"✓ {label}" if status == current_task.get('status', 'NOT_STARTED') else label
        status_buttons.append(
            InlineKeyboardButton(
                display_label,
                callback_data=f"status:{user_id}:{current_task['id']}:{status}"
            )
        )
    keyboard.append(status_buttons)
    
    # Add navigation buttons
    nav_buttons = []
    if total_pages > 1:
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page:{user_id}:{page-1}:{filter_type}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page:{user_id}:{page+1}:{filter_type}"))
        keyboard.append(nav_buttons)
    
    # Add filter buttons
    keyboard.append([
        InlineKeyboardButton("📋 All Tasks", callback_data=f"filter:all:{user_id}:0"),
        InlineKeyboardButton("✅ Done", callback_data=f"filter:done:{user_id}:0"),
        InlineKeyboardButton("⏳ Not Started", callback_data=f"filter:not_started:{user_id}:0"),
        InlineKeyboardButton("🔄 Pending", callback_data=f"filter:pending:{user_id}:0")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message, reply_markup=reply_markup)

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tasks for the user."""
    user_id = str(update.effective_user.id)
    await show_tasks_page(update, context, user_id, 0, 'all')

async def handle_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination navigation."""
    query = update.callback_query
    await query.answer()
    
    try:
        _, user_id, page, filter_type = query.data.split(':')
        await show_tasks_page(update, context, user_id, int(page), filter_type)
    except Exception as e:
        logger.error(f"Error in pagination: {str(e)}")
        await query.edit_message_text("Error navigating pages.")

async def filter_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Filter tasks by status."""
    query = update.callback_query
    await query.answer()
    
    try:
        # Parse the callback data with the new format
        parts = query.data.split(':')
        if len(parts) != 4:
            raise ValueError("Invalid callback data format")
            
        # The format is "filter:type:user_id:page"
        filter_type = parts[1]  # Changed from parts[0] to parts[1]
        user_id = parts[2]      # Changed from parts[1] to parts[2]
        page = int(parts[3])    # Changed from parts[2] to parts[3]
        
        logger.info(f"Filtering tasks for user {user_id} with filter type {filter_type}")
        
        # Reset to first page when filtering
        await show_tasks_page(update, context, user_id, 0, filter_type)
    except Exception as e:
        logger.error(f"Error filtering tasks: {str(e)}")
        await query.edit_message_text("Error filtering tasks. Please try again.")

async def update_task_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle task status update from inline buttons."""
    query = update.callback_query
    await query.answer()
    
    try:
        # Parse the callback data
        _, user_id, task_id, new_status = query.data.split(':')
        task_id = int(task_id)
        
        # Load tasks
        tasks = load_tasks()
        if user_id not in tasks:
            await query.edit_message_text("No tasks found.")
            return
            
        # Find and update the task
        task = next((t for t in tasks[user_id] if t['id'] == task_id), None)
        if not task:
            await query.edit_message_text("Task not found.")
            return
            
        # Update status
        old_status = task.get('status', 'NOT_STARTED')
        task['status'] = new_status
        save_tasks(tasks)
        
        logger.info(f"Updated task {task_id} status from {old_status} to {new_status}")
        
        # Get current page and filter type from the message
        current_page = 0
        filter_type = 'all'
        if query.message.text:
            if "Page" in query.message.text:
                page_info = query.message.text.split("Page")[1].split("of")[0].strip()
                current_page = int(page_info) - 1
            if "Tasks" in query.message.text:
                filter_type = query.message.text.split("Tasks")[0].strip().lower().replace(" ", "_")
        
        # Show the updated page
        await show_tasks_page(update, context, user_id, current_page, filter_type)
        
    except Exception as e:
        logger.error(f"Error updating task status: {str(e)}")
        await query.edit_message_text("Error updating task status. Please try again.")

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
        # Get current time in user's timezone
        user_tz = get_user_timezone(user_id)
        current_utc = datetime.now(pytz.UTC)
        current_user_time = current_utc.astimezone(user_tz)
        
        logger.info(f"Sending reminder for task {task_id} at {current_user_time.strftime('%H:%M')} {user_tz.zone}")
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
            # Get current time in UTC
            current_utc = datetime.now(pytz.UTC)
            
            # Convert current UTC time to user's timezone
            user_timezone = get_user_timezone(user_id)
            current_user_time = current_utc.astimezone(user_timezone)
            
            # Parse the scheduled time in user's timezone
            scheduled_time = datetime.strptime(local_time, '%H:%M')
            scheduled_user_time = current_user_time.replace(
                hour=scheduled_time.hour,
                minute=scheduled_time.minute,
                second=0,
                microsecond=0
            )
            
            # Check if it's time to send the reminder
            if (current_user_time.hour == scheduled_user_time.hour and 
                current_user_time.minute == scheduled_user_time.minute):
                logger.info(f"Triggering reminder for task {task_id} at {local_time} {user_tz}")
                asyncio.run_coroutine_threadsafe(
                    send_task_reminder(user_id, task_id, task_description, is_recurring),
                    loop
                )
        except Exception as e:
            logger.error(f"Error in reminder function: {str(e)}")
    
    # Schedule the new job to run every minute
    schedule.every(1).minutes.do(send_reminder).tag(job_id)
    
    logger.info(f"Scheduled task {task_id} for user {user_id} at {local_time} {user_tz}")

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

async def handle_health_check(request):
    """Handle health check requests."""
    return web.Response(text="Bot is running!")

async def start_web_server():
    """Start the web server for health checks."""
    global web_app
    web_app = web.Application()
    web_app.router.add_get('/', handle_health_check)
    
    # Get port from environment variable or use default
    port = int(os.getenv('PORT', 8080))
    
    try:
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Web server started on port {port}")
    except OSError as e:
        if e.errno == 10048:  # Port already in use
            logger.warning(f"Port {port} is already in use. Trying alternative port...")
            # Try alternative ports
            for alt_port in range(8081, 8090):
                try:
                    site = web.TCPSite(runner, '0.0.0.0', alt_port)
                    await site.start()
                    logger.info(f"Web server started on alternative port {alt_port}")
                    break
                except OSError:
                    continue
            else:
                logger.error("Could not find an available port. Web server not started.")
                return
        else:
            logger.error(f"Error starting web server: {str(e)}")
            return

@backoff.on_exception(
    backoff.expo,
    (Conflict, NetworkError, TimedOut),
    max_tries=5,
    max_time=300
)
async def start_polling_with_retry(application):
    """Start polling with retry logic for handling conflicts."""
    try:
        # Initialize the application first
        await application.initialize()
        
        # Start the application
        await application.start()
        
        # Now start polling
        await application.updater.start_polling(
            poll_interval=1.0,
            timeout=30,
            bootstrap_retries=5,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    except Conflict as e:
        logger.warning(f"Conflict detected: {str(e)}. Retrying...")
        raise  # Let backoff handle the retry
    except Exception as e:
        logger.error(f"Error in polling: {str(e)}")
        raise

async def remove_all_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove all tasks for the user."""
    user_id = str(update.effective_user.id)
    tasks = load_tasks()
    
    if user_id not in tasks or not tasks[user_id]:
        await update.message.reply_text('You have no tasks to remove.')
        return
    
    # Create confirmation keyboard
    keyboard = [
        [
            InlineKeyboardButton("Yes, remove all", callback_data=f"confirm_remove_all:{user_id}"),
            InlineKeyboardButton("No, cancel", callback_data=f"cancel_remove_all:{user_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        'Are you sure you want to remove all your tasks?',
        reply_markup=reply_markup
    )

async def handle_remove_all_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirmation for removing all tasks."""
    query = update.callback_query
    await query.answer()
    
    action, user_id = query.data.split(':')
    
    if action == 'confirm_remove_all':
        tasks = load_tasks()
        if user_id in tasks:
            tasks[user_id] = []
            save_tasks(tasks)
            await query.edit_message_text('All tasks have been removed.')
        else:
            await query.edit_message_text('No tasks found to remove.')
    else:
        await query.edit_message_text('Task removal cancelled.')

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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Add command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("addtask", add_task))
        application.add_handler(CommandHandler("addonetime", add_one_time_task))
        application.add_handler(CommandHandler("listtasks", list_tasks))
        application.add_handler(CommandHandler("removetask", remove_task))
        application.add_handler(CommandHandler("stoptask", stop_task))
        application.add_handler(CommandHandler("settimezone", set_timezone))
        application.add_handler(CommandHandler("removeall", remove_all_tasks))

        # Add callback query handlers with updated patterns
        application.add_handler(CallbackQueryHandler(update_task_status, pattern=r'^status:\d+:\d+:\w+$'))
        application.add_handler(CallbackQueryHandler(handle_pagination, pattern=r'^page:\d+:-?\d+:\w+$'))
        application.add_handler(CallbackQueryHandler(handle_remove_all_confirmation, pattern=r'^(confirm|cancel)_remove_all:\d+$'))
        application.add_handler(CallbackQueryHandler(filter_tasks, pattern=r'^filter:\w+:\d+:\d+$'))

        # Add error handler
        async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Handle errors in the bot."""
            logger.error(f"Exception while handling an update: {context.error}")
            if isinstance(context.error, Conflict):
                logger.warning("Conflict detected. The bot will retry automatically.")
            elif isinstance(context.error, NetworkError):
                logger.warning("Network error detected. The bot will retry automatically.")
            elif isinstance(context.error, TimedOut):
                logger.warning("Request timed out. The bot will retry automatically.")

        application.add_error_handler(error_handler)

        # Start the scheduler in a separate thread
        scheduler_thread = threading.Thread(target=run_scheduler)
        scheduler_thread.daemon = True
        scheduler_thread.start()

        logger.info("Bot started successfully!")
        
        # Start the web server
        loop.run_until_complete(start_web_server())
        
        # Start the bot with retry logic
        loop.run_until_complete(start_polling_with_retry(application))
        
        # Keep the main thread alive
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        finally:
            if application.running:
                loop.run_until_complete(application.stop())
            loop.close()
            
    except Exception as e:
        logger.error(f"Error starting bot: {str(e)}")
        scheduler_running = False
        if application and application.running:
            asyncio.run(application.stop())
        sys.exit(1)

if __name__ == '__main__':
    main() 