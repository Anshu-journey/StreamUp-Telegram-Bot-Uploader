import os
import requests
import asyncio
from collections import deque
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from dotenv import load_dotenv
import tempfile
import uuid

# Load environment variables
load_dotenv()

# Telegram API credentials
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# StreamUP API key
STREAMUP_API_KEY = "aedaa8ffa39a43889f4dc7a800948d82"
STREAMUP_UPLOAD_URL = "https://api.streamup.cc/v1/upload"

# Upload queue and tracking
upload_queue = asyncio.Queue()
processing_lock = asyncio.Lock()
is_processing = False
active_tasks = {}  # task_id -> {user_id, status, file_name, cancel_event}
user_tasks = {}    # user_id -> [task_ids]

# Initialize the Pyrogram client
app = Client(
    "streamup_upload_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "👋 Hello! I'm StreamUP Upload Bot.\n\n"
        "Just send me any file and I'll upload it to StreamUP for you.\n"
        "I can handle files larger than 20MB thanks to Pyrogram's chunked downloading.\n\n"
        "✅ Queue system enabled - files will be processed one at a time.\n"
        "📝 Commands:\n"
        "/queue - View current queue status\n"
        "/list - List your uploads\n"
        "/cancel <task_id> - Cancel a specific upload\n"
        "/cancelall - Cancel all your uploads"
    )

@app.on_message(filters.command("queue"))
async def queue_status(client: Client, message: Message):
    queue_size = upload_queue.qsize()
    if queue_size == 0:
        await message.reply_text("Queue is empty. You can send files to upload.")
    else:
        await message.reply_text(f"Current queue status: {queue_size} file(s) waiting to be processed.")

@app.on_message(filters.command("list"))
async def list_uploads(client: Client, message: Message):
    user_id = message.from_user.id
    
    # Check if user has any tasks
    if user_id not in user_tasks or not user_tasks[user_id]:
        await message.reply_text("You don't have any active uploads.")
        return
    
    # List all tasks for the user
    response = "Your uploads:\n\n"
    for task_id in user_tasks[user_id]:
        if task_id in active_tasks:
            task = active_tasks[task_id]
            status = task["status"]
            file_name = task["file_name"]
            response += f"📁 ID: `{task_id}`\n"
            response += f"   File: {file_name}\n"
            response += f"   Status: {status}\n\n"
    
    response += "To cancel an upload: `/cancel task_id`"
    await message.reply_text(response)

@app.on_message(filters.command("cancel"))
async def cancel_upload(client: Client, message: Message):
    user_id = message.from_user.id
    
    # Check if task ID was provided
    command_parts = message.text.split(' ', 1)
    if len(command_parts) < 2:
        await message.reply_text("Please provide a task ID to cancel. Use /list to see your uploads.")
        return
    
    task_id = command_parts[1].strip()
    
    # Check if task exists and belongs to user
    if task_id not in active_tasks or active_tasks[task_id]["user_id"] != user_id:
        await message.reply_text("Task not found or doesn't belong to you. Use /list to see your uploads.")
        return
    
    # Set the cancel event
    active_tasks[task_id]["cancel_event"].set()
    active_tasks[task_id]["status"] = "Cancelling..."
    await message.reply_text(f"Cancelling upload for task {task_id}...")

@app.on_message(filters.command("cancelall"))
async def cancel_all_uploads(client: Client, message: Message):
    user_id = message.from_user.id
    
    # Check if user has any tasks
    if user_id not in user_tasks or not user_tasks[user_id]:
        await message.reply_text("You don't have any active uploads to cancel.")
        return
    
    # Cancel all tasks for the user
    cancelled_count = 0
    for task_id in user_tasks[user_id]:
        if task_id in active_tasks:
            active_tasks[task_id]["cancel_event"].set()
            active_tasks[task_id]["status"] = "Cancelling..."
            cancelled_count += 1
    
    await message.reply_text(f"Cancelling all your uploads ({cancelled_count} tasks)...")

async def process_upload_queue():
    global is_processing
    
    while True:
        try:
            if not is_processing and not upload_queue.empty():
                is_processing = True
                task = await upload_queue.get()
                
                try:
                    await process_file(*task)
                except Exception as e:
                    print(f"Error processing queued file: {e}")
                finally:
                    upload_queue.task_done()
                    is_processing = False
            
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error in queue processor: {e}")
            await asyncio.sleep(5)

async def process_file(client, message, file, file_name, status_message, task_id):
    temp_path = None
    user_id = message.from_user.id
    
    try:
        # Check if task was cancelled
        if active_tasks[task_id]["cancel_event"].is_set():
            await status_message.edit_text("❌ Upload cancelled.")
            return
            
        # Update status
        active_tasks[task_id]["status"] = "Downloading"
        
        # Create a temporary file to store the download
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]) as temp_file:
            temp_path = temp_file.name
        
        # Download the file
        await status_message.edit_text(f"📥 Downloading {file_name}...\n(#{upload_queue.qsize() + 1} in queue)")
        await client.download_media(
            file, 
            temp_path, 
            progress=progress_callback(status_message, task_id)
        )
        
        # Check if task was cancelled during download
        if active_tasks[task_id]["cancel_event"].is_set():
            await status_message.edit_text("❌ Upload cancelled during download.")
            return
            
        # Update status
        active_tasks[task_id]["status"] = "Uploading"
        
        # Upload to StreamUP
        await status_message.edit_text(f"📤 Uploading {file_name} to StreamUP...")
        
        # Use the working method directly (API key in URL parameter)
        with open(temp_path, 'rb') as file_data:
            # Upload with API key as URL parameter which works successfully
            upload_url = f"{STREAMUP_UPLOAD_URL}?api_key={STREAMUP_API_KEY}"
            files = {'file': (file_name, file_data)}
            
            # Create a separate task for the upload to allow cancellation
            upload_task = asyncio.create_task(upload_file(upload_url, files))
            
            # Wait for either the upload to complete or cancellation
            while not upload_task.done():
                if active_tasks[task_id]["cancel_event"].is_set():
                    # Try to cancel the upload task
                    upload_task.cancel()
                    await status_message.edit_text("❌ Upload cancelled during upload.")
                    return
                await asyncio.sleep(1)
            
            response = upload_task.result()
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    if 'filecode' in result:
                        # Update status
                        active_tasks[task_id]["status"] = "Completed"
                        await status_message.edit_text(
                            f"✅ Upload successful!\n\n"
                            f"🔗 StreamUP Link: {result['filecode']}"
                        )
                    else:
                        # Update status
                        active_tasks[task_id]["status"] = "Failed"
                        await status_message.edit_text(f"❌ Upload error: {response.text}")
                except Exception as e:
                    # Update status
                    active_tasks[task_id]["status"] = "Failed"
                    await status_message.edit_text(f"❌ Error parsing response: {str(e)}")
            else:
                # Update status
                active_tasks[task_id]["status"] = "Failed"
                await status_message.edit_text(f"❌ Upload failed with status code {response.status_code}: {response.text}")
    
    except asyncio.CancelledError:
        # Handle cancellation
        await status_message.edit_text("❌ Task was cancelled.")
        active_tasks[task_id]["status"] = "Cancelled"
    except Exception as e:
        # Update status
        active_tasks[task_id]["status"] = "Failed"
        await status_message.edit_text(f"❌ Error: {str(e)}")
    
    finally:
        # Clean up the temporary file
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except PermissionError:
                # Wait a bit and retry
                import time
                time.sleep(1)
                try:
                    os.unlink(temp_path)
                except:
                    # If it still fails, just log it and continue
                    print(f"Could not delete temporary file: {temp_path}")
        
        # Remove completed/cancelled task after 5 minutes
        asyncio.create_task(cleanup_task(task_id, user_id, 300))
                    
        # Notify if more files in queue
        if not upload_queue.empty():
            queue_size = upload_queue.qsize()
            await message.reply_text(f"✅ This file is complete. {queue_size} more file(s) in the queue.")

async def cleanup_task(task_id, user_id, delay):
    """Remove the task from tracking after a delay"""
    await asyncio.sleep(delay)
    
    # Remove task from active_tasks
    if task_id in active_tasks:
        active_tasks.pop(task_id)
    
    # Remove task from user_tasks
    if user_id in user_tasks and task_id in user_tasks[user_id]:
        user_tasks[user_id].remove(task_id)
        
        # Remove user entry if no more tasks
        if not user_tasks[user_id]:
            user_tasks.pop(user_id)

async def upload_file(url, files):
    """Separate function for file upload to allow cancellation"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, 
        lambda: requests.post(url, files=files)
    )

# Progress callback function for download status updates
def progress_callback(status_message, task_id):
    async def callback(current, total):
        # Check if task has been cancelled
        if task_id in active_tasks and active_tasks[task_id]["cancel_event"].is_set():
            raise asyncio.CancelledError("Task cancelled")
            
        if total > 0:
            percentage = current * 100 // total
            progress_text = f"📥 Downloading: {percentage}% complete\n"
            progress_text += f"[{'■' * (percentage // 10)}{'□' * (10 - percentage // 10)}]\n"
            progress_text += f"ID: {task_id} (use /cancel {task_id} to cancel)"
            try:
                await status_message.edit_text(progress_text)
            except:
                pass
    return callback

@app.on_message(filters.document | filters.video | filters.audio | filters.photo)
async def handle_file(client: Client, message: Message):
    # Send initial message
    status_message = await message.reply_text("Adding to upload queue...")
    
    try:
        # Get file info
        file_name = None
        if message.document:
            file_name = message.document.file_name
            file = message.document
        elif message.video:
            file_name = message.video.file_name
            file = message.video
        elif message.audio:
            file_name = message.audio.file_name
            file = message.audio
        elif message.photo:
            file_name = f"photo_{message.photo.file_unique_id}.jpg"
            file = message.photo.file_id
        
        if not file_name:
            file_name = "unknown_file"
        
        # Create a unique task ID
        task_id = str(uuid.uuid4())[:8]  # Short UUID
        user_id = message.from_user.id
        
        # Create cancel event
        cancel_event = asyncio.Event()
        
        # Add to tracking
        active_tasks[task_id] = {
            "user_id": user_id,
            "status": "Queued",
            "file_name": file_name,
            "cancel_event": cancel_event
        }
        
        # Add to user's tasks
        if user_id not in user_tasks:
            user_tasks[user_id] = []
        user_tasks[user_id].append(task_id)
        
        # Add to queue
        current_queue_size = upload_queue.qsize()
        await upload_queue.put((client, message, file, file_name, status_message, task_id))
        
        if current_queue_size > 0:
            await status_message.edit_text(
                f"📋 Added to queue. Position: #{current_queue_size + 1}\n"
                f"File: {file_name}\n"
                f"Task ID: `{task_id}` (use /cancel {task_id} to cancel)\n"
                f"Your file will be processed automatically."
            )
        else:
            await status_message.edit_text(
                f"📋 Processing immediately: {file_name}\n"
                f"Task ID: `{task_id}` (use /cancel {task_id} to cancel)"
            )
            
    except Exception as e:
        await status_message.edit_text(f"❌ Error adding to queue: {str(e)}")

if __name__ == "__main__":
    print("Starting StreamUP Upload Bot with queue system...")
    app.start()
    loop = asyncio.get_event_loop()
    
    # Start the queue processor
    loop.create_task(process_upload_queue())
    
    # Run the client
    try:
        idle()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        app.stop()
        print("Bot stopped.") 