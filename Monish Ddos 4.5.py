import asyncio
import ipaddress
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters
import socket
import threading
import os

# Bot Configuration - Yahan apna bot token aur chat ID daalein
BOT_TOKEN = "8205829700:AAHYCJ0rMOuBh7NLaVHZ4jzB44sU7M2k0QQ"  # Apna bot token yahan daalein
CHAT_ID = "7412188979"  # Apna chat ID yahan daalein

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Active freeze sessions ko track karne ke liye
active_freezes = {}

class ServerFreezer:
    def __init__(self):
        self.freeze_processes = {}
        self.rules = {}
        
    def freeze_server(self, ip: str, port: int, duration: int, chat_id: str) -> bool:
        """
        Server ko freeze karne ka function using iptables (Linux) ya Windows firewall
        """
        try:
            # IP address validate karein
            ipaddress.ip_address(ip)
            
            # Duration validate karein
            if duration <= 0:
                return False
                
            # Platform detect karein
            if sys.platform.startswith('linux'):
                return self._freeze_linux(ip, port, duration, chat_id)
            elif sys.platform == 'win32':
                return self._freeze_windows(ip, port, duration, chat_id)
            else:
                logger.error(f"Unsupported platform: {sys.platform}")
                return False
                
        except Exception as e:
            logger.error(f"Error in freeze_server: {e}")
            return False
    
    def _freeze_linux(self, ip: str, port: int, duration: int, chat_id: str) -> bool:
        """Linux system ke liye iptables rules"""
        try:
            # Existing rule check karein
            check_cmd = f"iptables -L INPUT -n | grep 'DROP.*{ip}.*dpt:{port}'"
            check_result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
            
            if check_result.stdout.strip():
                logger.info(f"Rule already exists for {ip}:{port}")
                return True
            
            # Drop rule add karein
            rule_cmd = f"iptables -A INPUT -s {ip} -p tcp --dport {port} -j DROP"
            subprocess.run(rule_cmd, shell=True, check=True)
            
            # Save rule for persistence
            subprocess.run("iptables-save > /etc/iptables/rules.v4", shell=True)
            
            # Unfreeze thread start karein
            timer_thread = threading.Thread(
                target=self._unfreeze_linux_timer,
                args=(ip, port, duration, chat_id)
            )
            timer_thread.daemon = True
            timer_thread.start()
            
            # Store freeze info
            key = f"{ip}:{port}"
            self.freeze_processes[key] = {
                'start_time': datetime.now(),
                'duration': duration,
                'chat_id': chat_id,
                'thread': timer_thread
            }
            
            logger.info(f"Server frozen: {ip}:{port} for {duration} seconds")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Linux freeze error: {e}")
            return False
        except Exception as e:
            logger.error(f"Linux freeze error: {e}")
            return False
    
    def _unfreeze_linux_timer(self, ip: str, port: int, duration: int, chat_id: str):
        """Linux rule remove karne ka timer function"""
        time.sleep(duration)
        try:
            # Rule remove karein
            remove_cmd = f"iptables -D INPUT -s {ip} -p tcp --dport {port} -j DROP"
            subprocess.run(remove_cmd, shell=True, check=True)
            
            # Updated rules save karein
            subprocess.run("iptables-save > /etc/iptables/rules.v4", shell=True)
            
            # Tracking se remove karein
            key = f"{ip}:{port}"
            if key in self.freeze_processes:
                del self.freeze_processes[key]
            
            logger.info(f"Server unfrozen: {ip}:{port}")
            
            # Telegram notification send karein
            asyncio.run(self._send_telegram_message(
                f"✅ Server {ip}:{port} has been unfrozen after {duration} seconds.",
                chat_id
            ))
            
        except Exception as e:
            logger.error(f"Unfreeze error for {ip}:{port}: {e}")
    
    def _freeze_windows(self, ip: str, port: int, duration: int, chat_id: str) -> bool:
        """Windows system ke liye netsh advfirewall rules"""
        try:
            # Rule name create karein
            rule_name = f"Freeze_{ip.replace('.','_')}_{port}_{int(time.time())}"
            
            # Block rule add karein
            add_cmd = f'netsh advfirewall firewall add rule name="{rule_name}" dir=in action=block protocol=TCP localport={port} remoteip={ip}'
            subprocess.run(add_cmd, shell=True, check=True)
            
            # Unfreeze timer thread start karein
            timer_thread = threading.Thread(
                target=self._unfreeze_windows_timer,
                args=(rule_name, ip, port, duration, chat_id)
            )
            timer_thread.daemon = True
            timer_thread.start()
            
            # Store freeze info
            key = f"{ip}:{port}"
            self.freeze_processes[key] = {
                'start_time': datetime.now(),
                'duration': duration,
                'chat_id': chat_id,
                'thread': timer_thread,
                'rule_name': rule_name
            }
            
            logger.info(f"Server frozen (Windows): {ip}:{port} for {duration} seconds")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Windows freeze error: {e}")
            return False
        except Exception as e:
            logger.error(f"Windows freeze error: {e}")
            return False
    
    def _unfreeze_windows_timer(self, rule_name: str, ip: str, port: int, duration: int, chat_id: str):
        """Windows rule remove karne ka timer function"""
        time.sleep(duration)
        try:
            # Rule delete karein
            delete_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}"'
            subprocess.run(delete_cmd, shell=True, check=True)
            
            # Tracking se remove karein
            key = f"{ip}:{port}"
            if key in self.freeze_processes:
                del self.freeze_processes[key]
            
            logger.info(f"Server unfrozen (Windows): {ip}:{port}")
            
            # Telegram notification
            asyncio.run(self._send_telegram_message(
                f"✅ Server {ip}:{port} has been unfrozen after {duration} seconds.",
                chat_id
            ))
            
        except Exception as e:
            logger.error(f"Windows unfreeze error for {ip}:{port}: {e}")
    
    async def _send_telegram_message(self, message: str, chat_id: str):
        """Telegram message send karne ka helper function"""
        try:
            application = Application.builder().token(BOT_TOKEN).build()
            await application.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

# Global freezer instance
freezer = ServerFreezer()

# Telegram command handlers
async def start(update: Update, context: CallbackContext):
    """Start command handler"""
    keyboard = [
        [
            InlineKeyboardButton("📋 Freeze Server", callback_data='freeze'),
            InlineKeyboardButton("📊 Status", callback_data='status')
        ],
        [
            InlineKeyboardButton("❌ Unfreeze All", callback_data='unfreeze_all'),
            InlineKeyboardButton("ℹ️ Help", callback_data='help')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🚀 *Server Freezer Bot*\n\n"
        "Main server ko freeze kar sakta hoon specified duration ke liye.\n\n"
        "Commands:\n"
        "/freeze <IP> <PORT> <DURATION> - Server freeze karein (duration in seconds)\n"
        "/status - Active freeze check karein\n"
        "/unfreeze - Specific server unfreeze karein\n"
        "/unfreeze_all - Sabhi server unfreeze karein\n"
        "/help - Help menu",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def freeze_command(update: Update, context: CallbackContext):
    """Freeze command handler - /freeze IP PORT DURATION"""
    try:
        args = context.args
        if len(args) < 3:
            await update.message.reply_text(
                "❌ Invalid format!\n"
                "Usage: /freeze <IP> <PORT> <DURATION>\n"
                "Example: /freeze 192.168.1.100 80 300"
            )
            return
        
        ip = args[0]
        port = int(args[1])
        duration = int(args[2])
        
        # Duration validate karein (min 10 sec, max 600 sec)
        if duration < 10:
            await update.message.reply_text("❌ Duration minimum 10 seconds hona chahiye!")
            return
        if duration > 600:
            await update.message.reply_text("❌ Duration maximum 600 seconds (10 minutes) ho sakti hai!")
            return
        
        # Freeze execute karein
        chat_id = str(update.effective_chat.id)
        success = freezer.freeze_server(ip, port, duration, chat_id)
        
        if success:
            await update.message.reply_text(
                f"✅ *Server Frozen*\n"
                f"🌐 IP: {ip}\n"
                f"🔌 Port: {port}\n"
                f"⏱️ Duration: {duration} seconds\n"
                f"⏰ Will unfreeze at: {(datetime.now() + timedelta(seconds=duration)).strftime('%H:%M:%S')}\n\n"
                f"🔄 Active freeze count: {len(freezer.freeze_processes)}",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Server freeze karne mein error aayi. Check logs!")
            
    except ValueError:
        await update.message.reply_text("❌ Invalid port or duration! Please enter numbers.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def status_command(update: Update, context: CallbackContext):
    """Status command - active freeze dikhaye"""
    if not freezer.freeze_processes:
        await update.message.reply_text("📊 No active freezes currently.")
        return
    
    status_text = "📊 *Active Freezes*\n\n"
    for key, info in freezer.freeze_processes.items():
        remaining = info['duration'] - (datetime.now() - info['start_time']).seconds
        if remaining < 0:
            remaining = 0
        status_text += f"• {key} - {remaining}s remaining\n"
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def unfreeze_command(update: Update, context: CallbackContext):
    """Unfreeze specific server"""
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Invalid format!\n"
                "Usage: /unfreeze <IP> <PORT>"
            )
            return
        
        ip = args[0]
        port = int(args[1])
        key = f"{ip}:{port}"
        
        if key not in freezer.freeze_processes:
            await update.message.reply_text(f"❌ No active freeze found for {ip}:{port}")
            return
        
        # Linux unfreeze
        if sys.platform.startswith('linux'):
            remove_cmd = f"iptables -D INPUT -s {ip} -p tcp --dport {port} -j DROP"
            subprocess.run(remove_cmd, shell=True, check=True)
            subprocess.run("iptables-save > /etc/iptables/rules.v4", shell=True)
        
        # Windows unfreeze
        elif sys.platform == 'win32':
            rule_name = freezer.freeze_processes[key].get('rule_name', '')
            if rule_name:
                delete_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}"'
                subprocess.run(delete_cmd, shell=True, check=True)
        
        # Remove from tracking
        del freezer.freeze_processes[key]
        
        await update.message.reply_text(
            f"✅ Server {ip}:{port} has been unfrozen successfully!"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Unfreeze error: {str(e)}")

async def unfreeze_all_command(update: Update, context: CallbackContext):
    """Sabhi freeze remove karein"""
    if not freezer.freeze_processes:
        await update.message.reply_text("❌ No active freezes to unfreeze.")
        return
    
    count = len(freezer.freeze_processes)
    
    # Sabhi freeze remove karein
    for key in list(freezer.freeze_processes.keys()):
        ip, port = key.split(':')
        try:
            if sys.platform.startswith('linux'):
                remove_cmd = f"iptables -D INPUT -s {ip} -p tcp --dport {int(port)} -j DROP"
                subprocess.run(remove_cmd, shell=True, check=True)
            elif sys.platform == 'win32':
                rule_name = freezer.freeze_processes[key].get('rule_name', '')
                if rule_name:
                    delete_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}"'
                    subprocess.run(delete_cmd, shell=True, check=True)
        except Exception as e:
            logger.error(f"Error unfreezing {key}: {e}")
    
    # Clear tracking
    freezer.freeze_processes.clear()
    
    await update.message.reply_text(f"✅ {count} server(s) unfrozen successfully!")

async def help_command(update: Update, context: CallbackContext):
    """Help command"""
    help_text = """
🤖 *Server Freezer Bot Help*

*Commands:*
• `/freeze <IP> <PORT> <DURATION>` - Server freeze karein
  Example: `/freeze 192.168.1.100 80 300`
  
• `/status` - Active freeze check karein
• `/unfreeze <IP> <PORT>` - Specific server unfreeze karein
• `/unfreeze_all` - Sabhi server unfreeze karein
• `/help` - This help menu

*Duration Options:*
• Minimum: 10 seconds
• Maximum: 600 seconds (10 minutes)
• Recommended: 120, 300, 500 seconds

*Supported Platforms:*
• Linux (iptables)
• Windows (netsh advfirewall)

*Features:*
• Automatic unfreeze after duration
• Real-time status tracking
• Notifications on unfreeze
• Multiple server support

⚠️ *Note:* Administrator privileges required!
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def button_callback(update: Update, context: CallbackContext):
    """Button callback handler"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'freeze':
        await query.edit_message_text(
            "📝 *Freeze Server*\n\n"
            "Command format:\n"
            "/freeze <IP> <PORT> <DURATION>\n\n"
            "Example: /freeze 192.168.1.100 80 300\n\n"
            "Duration options: 120, 300, 500 seconds",
            parse_mode='Markdown'
        )
    elif query.data == 'status':
        await status_command(update, context)
    elif query.data == 'unfreeze_all':
        await unfreeze_all_command(update, context)
    elif query.data == 'help':
        await help_command(update, context)

async def handle_message(update: Update, context: CallbackContext):
    """Text message handler for invalid commands"""
    await update.message.reply_text(
        "❌ Invalid command! Type /help to see available commands."
    )

def main():
    """Main function to run the bot"""
    try:
        # Application create karein
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("freeze", freeze_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("unfreeze", unfreeze_command))
        application.add_handler(CommandHandler("unfreeze_all", unfreeze_all_command))
        
        # Callback query handler
        application.add_handler(CallbackQueryHandler(button_callback))
        
        # Message handler for invalid commands
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Start bot
        logger.info("Bot is starting...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Bot startup error: {e}")

if __name__ == '__main__':
    main()