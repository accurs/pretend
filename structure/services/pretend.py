import config

from discord import (
    Intents, 
    AllowedMentions, 
    ClientUser,
    Activity,
    ActivityType,
    User,
    Message,
)
from discord.ext.commands import (
    Bot, 
    CooldownMapping, 
    MinimalHelpCommand
)

from aiohttp import ClientSession
from datetime import datetime
from typing import Dict, Collection, Optional
from logging import getLogger

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .redis import Redis
    from .database import Database


log = getLogger("pretend/bot")


class Pretend(Bot):
    session: ClientSession
    uptime: datetime
    traceback: Dict[str, Exception]
    global_cooldown: CooldownMapping
    owner_ids: Collection[int]
    database: "Database"
    redis: "Redis"
    user: ClientUser
    version: str = "0.1"

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            **kwargs,
            intents=Intents(
                guilds=True,
                members=True,
                messages=True,
                reactions=True,
                presences=True,
                moderation=True,
                voice_states=True,
                message_content=True,
                emojis_and_stickers=True,
            ),
            allowed_mentions=AllowedMentions(
                replied_user=False,
                everyone=False,
                roles=False,
                users=True,
            ),
            command_prefix=";",
            help_command=MinimalHelpCommand(
                verify_checks=False,
                command_attrs={
                    "aliases": ["h"],
                    "hidden": True,
                },
            ),
            case_insensitive=True,
            max_messages=1500,
            activity=Activity(
                type=ActivityType.custom,
                name=" ",
                state="🔗 pretend.cc",
            ),
        )

    @property
    def db(self) -> Database:
        return self.database

    @property
    def owner(self) -> User:
        return self.get_user(self.owner_ids[0])  # type: ignore

    def get_message(self, message_id: int) -> Optional[Message]:
        return self._connection._get_message(message_id)

    async def get_or_fetch_user(self, user_id: int) -> User:
        return self.get_user(user_id) or await self.fetch_user(user_id)
    
    def run(self) -> None:
        log.info("Starting the bot...")

        super().run(
            config.DISCORD.TOKEN,
            reconnect=True,
            log_handler=None,
        )

    async def close(self) -> None:
        await super().close()
        await self.session.close()