import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import asyncio
import aiohttp
import datetime
import logging
from typing import Optional, Union

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('roblox-ranking-bot')

# Default configuration
DEFAULT_CONFIG = {
    "token": os.getenv("BOTTOKEN"),
    "cookie":os.getenv("COOKIE"),
    "group_id": 9004585,  # Replace with your group ID
    "roles": {
        "ranking_permit": 123456789012345678,  # Role ID for ranking permissions
        "developer": 123456789012345678,  # Role ID for developer permissions
        "suspension_permit": 123456789012345678  # Role ID for suspension permissions
    },
    "suspension_rank_name": "Customer",  # Default suspension rank name
    "rank_bans": {},  # Will store: user_id: {"until": timestamp}
    "suspensions": {}  # Will store: user_id: {"until": timestamp, "original_rank": rank_id}
}

# Ensure config file exists or create it
def load_config():
    if not os.path.exists('config.json'):
        with open('config.json', 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        print("Config file created! Please fill in your details in config.json before running the bot again.")
        exit(0)
    
    with open('config.json', 'r') as f:
        return json.load(f)

CONFIG = load_config()

# Save configuration changes
def save_config():
    with open('config.json', 'w') as f:
        json.dump(CONFIG, f, indent=4)

# Roblox API wrapper class
class RobloxAPI:
    def __init__(self, cookie):
        self.cookie = cookie
        self.headers = {
            'Cookie': f'.ROBLOSECURITY={cookie}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Discord Ranking Bot'
        }
        self.session = None
        self.csrf_token = None
        self.user_id = None
        self.username = None
        self.group_roles = {}
    
    async def initialize(self):
        self.session = aiohttp.ClientSession()
        await self.get_csrf_token()
        await self.get_auth_user_info()
        await self.get_group_roles()
        logger.info(f"Initialized Roblox API as {self.username} (ID: {self.user_id})")
    
    async def close(self):
        if self.session:
            await self.session.close()
    
    async def get_csrf_token(self):
        async with self.session.post(
            'https://auth.roblox.com/v2/logout',
            headers=self.headers,
            allow_redirects=False
        ) as response:
            self.csrf_token = response.headers.get('x-csrf-token')
            if self.csrf_token:
                self.headers['X-CSRF-TOKEN'] = self.csrf_token
            else:
                logger.error("Failed to get CSRF token")
                raise Exception("Failed to get CSRF token")
    
    async def get_auth_user_info(self):
        async with self.session.get(
            'https://users.roblox.com/v1/users/authenticated',
            headers=self.headers
        ) as response:
            if response.status == 200:
                data = await response.json()
                self.user_id = data.get('id')
                self.username = data.get('name')
            else:
                error_text = await response.text()
                logger.error(f"Failed to get authenticated user info: {error_text}")
                raise Exception("Invalid Roblox cookie or authentication failed")
    
    async def get_group_roles(self):
        async with self.session.get(
            f'https://groups.roblox.com/v1/groups/{CONFIG["group_id"]}/roles',
            headers=self.headers
        ) as response:
            if response.status == 200:
                data = await response.json()
                self.group_roles = {role['name']: role for role in data.get('roles', [])}
                logger.info(f"Loaded {len(self.group_roles)} group roles")
            else:
                error_text = await response.text()
                logger.error(f"Failed to get group roles: {error_text}")
                raise Exception(f"Failed to get roles for group {CONFIG['group_id']}")
    
    async def get_user_id_from_username(self, username):
        async with self.session.post(
            'https://users.roblox.com/v1/usernames/users',
            headers=self.headers,
            json={"usernames": [username], "excludeBannedUsers": True}
        ) as response:
            if response.status == 200:
                data = await response.json()
                users = data.get('data', [])
                if users:
                    return users[0].get('id')
            return None
    
    async def get_user_info(self, user_identifier):
        """Get user info by either username or user ID"""
        user_id = None
        username = None
        
        # Check if user_identifier is a user ID (numeric)
        if str(user_identifier).isdigit():
            user_id = int(user_identifier)
            url = f'https://users.roblox.com/v1/users/{user_id}'
            async with self.session.get(url, headers=self.headers) as response:
                if response.status == 200:
                    data = await response.json()
                    username = data.get('name')
                else:
                    return None, None
        else:
            # Assume it's a username
            username = user_identifier
            user_id = await self.get_user_id_from_username(username)
            
        return user_id, username
    
    async def get_user_rank(self, user_id):
        async with self.session.get(
            f'https://groups.roblox.com/v2/users/{user_id}/groups/roles',
            headers=self.headers
        ) as response:
            if response.status == 200:
                data = await response.json()
                for group in data.get('data', []):
                    if group.get('group', {}).get('id') == CONFIG['group_id']:
                        role = group.get('role', {})
                        return {
                            'id': role.get('id'),
                            'name': role.get('name'),
                            'rank': role.get('rank')
                        }
            return None
    
    async def set_rank(self, user_id, role_id):
        async with self.session.patch(
            f'https://groups.roblox.com/v1/groups/{CONFIG["group_id"]}/users/{user_id}',
            headers=self.headers,
            json={"roleId": role_id}
        ) as response:
            success = response.status == 200
            if not success:
                error_text = await response.text()
                logger.error(f"Failed to set rank: {error_text}")
            return success
    
    async def set_group_shout(self, message):
        async with self.session.patch(
            f'https://groups.roblox.com/v1/groups/{CONFIG["group_id"]}/status',
            headers=self.headers,
            json={"message": message}
        ) as response:
            success = response.status == 200
            if not success:
                error_text = await response.text()
                logger.error(f"Failed to set group shout: {error_text}")
            return success
    
    async def clear_group_shout(self):
        return await self.set_group_shout("")

# Bot class
class RobloxRankingBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        self.roblox_api = RobloxAPI(CONFIG['cookie'])
        
    async def setup_hook(self):
        await self.roblox_api.initialize()
        self.check_expirations.start()
        await self.tree.sync()
        logger.info("Bot commands synced")
    
    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        await self.change_presence(activity=discord.Game(name="/rank | Roblox Ranking"))
    
    @tasks.loop(minutes=5)
    async def check_expirations(self):
        """Check for expired rank bans and suspensions"""
        current_time = datetime.datetime.now().timestamp()
        
        # Check rank bans
        expired_bans = []
        for user_id, ban_info in CONFIG['rank_bans'].items():
            if ban_info['until'] < current_time:
                expired_bans.append(user_id)
        
        for user_id in expired_bans:
            del CONFIG['rank_bans'][user_id]
            logger.info(f"Rank ban expired for user ID {user_id}")
        
        # Check suspensions
        expired_suspensions = []
        for user_id, suspension_info in CONFIG['suspensions'].items():
            if suspension_info['until'] < current_time:
                expired_suspensions.append((user_id, suspension_info['original_rank']))
        
        for user_id, original_rank in expired_suspensions:
            # Restore original rank
            success = await self.roblox_api.set_rank(user_id, original_rank)
            if success:
                del CONFIG['suspensions'][user_id]
                logger.info(f"Suspension expired for user ID {user_id}, restored to rank {original_rank}")
            else:
                logger.error(f"Failed to restore rank for user ID {user_id}")
        
        # Save changes if any expirations were processed
        if expired_bans or expired_suspensions:
            save_config()
    
    @check_expirations.before_loop
    async def before_check_expirations(self):
        await self.wait_until_ready()

# Create bot instance
bot = RobloxRankingBot()

# Helper functions
def has_permission(interaction, permission_type):
    """Check if a user has the specified permission based on role"""
    if not interaction.guild:
        return False
    
    role_id = CONFIG['roles'].get(permission_type)
    if not role_id:
        return False
    
    role = interaction.guild.get_role(role_id)
    if not role:
        return False
    
    # Developer role can do everything
    dev_role_id = CONFIG['roles'].get('developer')
    if dev_role_id:
        dev_role = interaction.guild.get_role(dev_role_id)
        if dev_role and dev_role in interaction.user.roles:
            return True
    
    return role in interaction.user.roles

def parse_time(time_str):
    """Parse time string like '30d' or '12h' into seconds"""
    if not time_str:
        return 0
    
    unit = time_str[-1].lower()
    try:
        value = int(time_str[:-1])
    except ValueError:
        return 0
    
    if unit == 'd':
        return value * 86400  # days to seconds
    elif unit == 'h':
        return value * 3600   # hours to seconds
    elif unit == 'm':
        return value * 60     # minutes to seconds
    elif unit == 's':
        return value          # seconds
    else:
        return 0

# Command Group
@bot.tree.command(name="getrank", description="Get the rank of a user in the group")
@app_commands.describe(user_identifier="Username or user ID of the Roblox user")
async def get_rank(interaction: discord.Interaction, user_identifier: str):
    if not has_permission(interaction, 'ranking_permit') and not has_permission(interaction, 'developer'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=False)
    
    user_id, username = await bot.roblox_api.get_user_info(user_identifier)
    if not user_id:
        await interaction.followup.send(f"Couldn't find Roblox user: {user_identifier}")
        return
    
    rank_info = await bot.roblox_api.get_user_rank(user_id)
    if not rank_info:
        await interaction.followup.send(f"{username} (ID: {user_id}) is not a member of the group.")
        return
    
    embed = discord.Embed(
        title=f"User Rank Information",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )
    embed.add_field(name="Username", value=username, inline=True)
    embed.add_field(name="User ID", value=user_id, inline=True)
    embed.add_field(name="Rank", value=f"{rank_info['name']} ({rank_info['rank']})", inline=False)
    embed.set_footer(text=f"Requested by {interaction.user.name}")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="rank", description="Rank a user in the group")
@app_commands.describe(user_identifier="Username or user ID of the Roblox user")
async def rank_user(interaction: discord.Interaction, user_identifier: str):
    if not has_permission(interaction, 'ranking_permit') and not has_permission(interaction, 'developer'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=False)
    
    user_id, username = await bot.roblox_api.get_user_info(user_identifier)
    if not user_id:
        await interaction.followup.send(f"Couldn't find Roblox user: {user_identifier}")
        return
    
    # Check if user is rank banned
    if str(user_id) in CONFIG['rank_bans']:
        ban_info = CONFIG['rank_bans'][str(user_id)]
        expiry_date = datetime.datetime.fromtimestamp(ban_info['until']).strftime('%Y-%m-%d %H:%M:%S')
        await interaction.followup.send(f"This user is rank banned until {expiry_date}.")
        return
    
    # Get current user rank
    current_rank = await bot.roblox_api.get_user_rank(user_id)
    if not current_rank:
        await interaction.followup.send(f"{username} (ID: {user_id}) is not a member of the group.")
        return
    
    # Create a select menu with all group roles
    select_options = []
    
    # Sort roles by rank number
    sorted_roles = sorted(bot.roblox_api.group_roles.values(), key=lambda x: x['rank'])
    
    for role in sorted_roles:
        # Add option
        select_options.append(
            discord.SelectOption(
                label=role['name'],
                description=f"Rank: {role['rank']}",
                value=str(role['id']),
                default=role['id'] == current_rank['id']
            )
        )
    
    # Create the select menu
    select = discord.ui.Select(
        placeholder="Select a rank",
        min_values=1,
        max_values=1,
        options=select_options
    )
    
    view = discord.ui.View(timeout=60)
    
    async def select_callback(interaction: discord.Interaction):
        selected_role_id = int(select.values[0])
        selected_role = next((r for r in bot.roblox_api.group_roles.values() if r['id'] == selected_role_id), None)
        
        # Set the user's rank
        success = await bot.roblox_api.set_rank(user_id, selected_role_id)
        
        if success:
            embed = discord.Embed(
                title="Rank Update Successful",
                description=f"Successfully ranked {username} to {selected_role['name']}",
                color=discord.Color.green(),
                timestamp=datetime.datetime.now()
            )
            embed.add_field(name="Previous Rank", value=current_rank['name'], inline=True)
            embed.add_field(name="New Rank", value=selected_role['name'], inline=True)
            embed.set_footer(text=f"Ranked by {interaction.user.name}")
            
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        else:
            await interaction.response.edit_message(content=f"Failed to set rank for {username}.", view=None)
    
    select.callback = select_callback
    view.add_item(select)
    
    await interaction.followup.send(f"Select a rank for {username} (Current rank: {current_rank['name']}):", view=view)

@bot.tree.command(name="rankban", description="Ban a user from being ranked for a period of time")
@app_commands.describe(
    user_identifier="Username or user ID of the Roblox user",
    duration="Duration of the ban (e.g., 180d, 24h, 30m, 60s)"
)
async def rank_ban(interaction: discord.Interaction, user_identifier: str, duration: str):
    if not has_permission(interaction, 'developer'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=False)
    
    user_id, username = await bot.roblox_api.get_user_info(user_identifier)
    if not user_id:
        await interaction.followup.send(f"Couldn't find Roblox user: {user_identifier}")
        return
    
    # Calculate ban expiry time
    duration_seconds = parse_time(duration)
    if duration_seconds <= 0:
        await interaction.followup.send("Invalid duration format. Use formats like 180d, 24h, 30m, 60s.")
        return
    
    expiry_timestamp = datetime.datetime.now().timestamp() + duration_seconds
    expiry_date = datetime.datetime.fromtimestamp(expiry_timestamp).strftime('%Y-%m-%d %H:%M:%S')
    
    # Add rank ban
    CONFIG['rank_bans'][str(user_id)] = {"until": expiry_timestamp}
    save_config()
    
    await interaction.followup.send(f"Rank banned {username} (ID: {user_id}) until {expiry_date}.")

@bot.tree.command(name="suspend", description="Suspend a user by demoting them temporarily")
@app_commands.describe(
    user_identifier="Username or user ID of the Roblox user",
    duration="Duration of the suspension (e.g., 180d, 24h, 30m, 60s)"
)
async def suspend_user(interaction: discord.Interaction, user_identifier: str, duration: str):
    if not has_permission(interaction, 'suspension_permit') and not has_permission(interaction, 'developer'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=False)
    
    user_id, username = await bot.roblox_api.get_user_info(user_identifier)
    if not user_id:
        await interaction.followup.send(f"Couldn't find Roblox user: {user_identifier}")
        return
    
    # Calculate suspension expiry time
    duration_seconds = parse_time(duration)
    if duration_seconds <= 0:
        await interaction.followup.send("Invalid duration format. Use formats like 180d, 24h, 30m, 60s.")
        return
    
    # Get current user rank
    current_rank = await bot.roblox_api.get_user_rank(user_id)
    if not current_rank:
        await interaction.followup.send(f"{username} (ID: {user_id}) is not a member of the group.")
        return
    
    # Find suspension rank
    suspension_rank_name = CONFIG.get('suspension_rank_name', 'Customer')
    suspension_rank = bot.roblox_api.group_roles.get(suspension_rank_name)
    
    if not suspension_rank:
        await interaction.followup.send(f"Suspension rank '{suspension_rank_name}' not found in the group.")
        return
    
    # Don't suspend if they are already at or below the suspension rank
    if current_rank['rank'] <= suspension_rank['rank']:
        await interaction.followup.send(f"{username} is already at or below the suspension rank.")
        return
    
    # Set the user's rank to the suspension rank
    success = await bot.roblox_api.set_rank(user_id, suspension_rank['id'])
    
    if success:
        expiry_timestamp = datetime.datetime.now().timestamp() + duration_seconds
        expiry_date = datetime.datetime.fromtimestamp(expiry_timestamp).strftime('%Y-%m-%d %H:%M:%S')
        
        # Store suspension info
        CONFIG['suspensions'][str(user_id)] = {
            "until": expiry_timestamp,
            "original_rank": current_rank['id']
        }
        save_config()
        
        embed = discord.Embed(
            title="User Suspended",
            description=f"{username} has been suspended until {expiry_date}",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Previous Rank", value=current_rank['name'], inline=True)
        embed.add_field(name="Suspension Rank", value=suspension_rank['name'], inline=True)
        embed.set_footer(text=f"Suspended by {interaction.user.name}")
        
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send(f"Failed to suspend {username}.")

@bot.tree.command(name="groupshout", description="Set the group shout message")
@app_commands.describe(message="The message to set as the group shout")
async def group_shout(interaction: discord.Interaction, message: str):
    if not has_permission(interaction, 'developer'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=False)
    
    success = await bot.roblox_api.set_group_shout(message)
    
    if success:
        await interaction.followup.send(f"Group shout updated successfully!")
    else:
        await interaction.followup.send("Failed to update group shout.")

@bot.tree.command(name="cleargroupshout", description="Clear the group shout message")
async def clear_group_shout(interaction: discord.Interaction):
    if not has_permission(interaction, 'developer'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=False)
    
    success = await bot.roblox_api.clear_group_shout()
    
    if success:
        await interaction.followup.send("Group shout cleared successfully!")
    else:
        await interaction.followup.send("Failed to clear group shout.")

@bot.tree.command(name="setbotplaying", description="Set the bot's playing status")
@app_commands.describe(message="The message to set as the bot's playing status")
async def set_bot_playing(interaction: discord.Interaction, message: str):
    if not has_permission(interaction, 'developer'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await bot.change_presence(activity=discord.Game(name=message))
    await interaction.response.send_message(f"Bot status updated to: Playing {message}")

@bot.tree.command(name="resetbot", description="Reset the bot's status and refresh the Roblox API session")
async def reset_bot(interaction: discord.Interaction):
    if not has_permission(interaction, 'developer'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=False)
    
    # Close the current session
    await bot.roblox_api.close()
    
    # Reinitialize
    try:
        await bot.roblox_api.initialize()
        await bot.change_presence(activity=discord.Game(name="/rank | Roblox Ranking"))
        await interaction.followup.send("Bot reset successfully. Roblox API session refreshed.")
    except Exception as e:
        logger.error(f"Error resetting bot: {e}")
        await interaction.followup.send(f"Failed to reset bot: {str(e)}")

# Run the bot
def main():
    try:
        bot.run(CONFIG['token'])
    except Exception as e:
        logger.error(f"Error running bot: {e}")
    finally:
        # Ensure we close the API session
        if bot.roblox_api and bot.roblox_api.session:
            asyncio.run(bot.roblox_api.close())


@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"Bot is ready! Synced {len(synced)} commands.")

if __name__ == "__main__":
    main()
