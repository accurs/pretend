from __future__ import annotations

import discord

from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Optional,
    Type,
    cast,
    TypeVar,
)
from datetime import datetime
from io import BytesIO
from aiomisc import PeriodicCallback
from aiohttp import ClientSession
from contextlib import suppress

from discord import (
    ButtonStyle,
    Colour,
    File,
    Guild,
    HTTPException,
    Member,
    Message,
    TextChannel,
    Thread,
    VoiceChannel,
    PartialMessage,
    Interaction,
    Color,
)
from discord.context_managers import Typing as DefaultTyping
from discord.ext.commands import Command
from discord.ext.commands import Context as OriginalContext
from discord.ext.commands import UserInputError
from discord.types.embed import EmbedType
from discord.ui import button, View as OriginalView, Button as OriginalButton
from discord.utils import cached_property

if TYPE_CHECKING:
    from structure.services.pretend import Pretend
    from structure.services.database import Database
    from structure.services.redis import Redis
    from types import TracebackType

BE = TypeVar("BE", bound=BaseException)

class View(OriginalView):
    ctx: Context

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def callback(self, interaction: Interaction, button: OriginalButton):
        raise NotImplementedError

    async def disable_buttons(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore

    async def on_timeout(self) -> None:
        self.stop()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user != self.ctx.author:
            embed = Embed(
                description=f"This is {self.ctx.author.mention}'s selection!",
                color=Color.dark_embed(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        return interaction.user == self.ctx.author

class Confirmation(View):
    value: Optional[bool]

    def __init__(self, ctx: Context, *, timeout: Optional[int] = 60):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.value = None

    @button(label="Approve", style=ButtonStyle.green)
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.value = True
        self.stop()

    @button(label="Decline", style=ButtonStyle.danger)
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.value = False
        self.stop()


class Typing(DefaultTyping):
    ctx: Context

    def __init__(self, ctx: Context):
        super().__init__(ctx.channel)
        self.ctx = ctx

    async def do_typing(self) -> None:
        if self.ctx.settings.reskin:
            return

        return await super().do_typing()


class Loading:
    callback: Optional[PeriodicCallback]
    ctx: Context
    channel: VoiceChannel | TextChannel | Thread

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.channel = ctx.channel
        self.callback = None

    @property
    def redis(self) -> Redis:
        return self.ctx.bot.redis

    async def locked(self) -> bool:
        if await self.redis.exists(self.key):
            return True

        await self.redis.set(self.key, 1, ex=30)
        return False

    async def task(self) -> None:
        if not self.ctx.response:
            return

        value = self.ctx.response.embeds[0].description  # type: ignore
        if not value:
            return

        value = value.replace("", "")
        if not value.endswith("..."):
            value += "."
        else:
            value = value.rstrip(".")

        await self.ctx.neutral(value, patch=self.ctx.response)

    async def __aenter__(self) -> None:
        if await self.locked():
            return

        self.callback = PeriodicCallback(self.task)
        self.callback.start(10, delay=2)

    async def __aexit__(
        self,
        exc_type: Optional[Type[BE]],
        exc: Optional[BE],
        traceback: Optional[TracebackType],
    ) -> None:
        await self.redis.delete(self.key)
        if self.callback:
            self.callback.stop()


class Context(OriginalContext):
    bot: "Pretend"
    guild: Guild
    author: Member
    channel: VoiceChannel | TextChannel | Thread
    command: Command[Any, ..., Any]
    response: Optional[Message] = None

    @property
    def session(self) -> ClientSession:
        return self.bot.session

    @property
    def db(self) -> Database:
        return self.bot.database

    @cached_property
    def replied_message(self) -> Optional[Message]:
        reference = self.message.reference
        if reference and isinstance(reference.resolved, Message):
            return reference.resolved

        return None

    def typing(self) -> Typing:
        return Typing(self)

    def loading(self, *args: str, **kwargs) -> Loading:
        if args:
            self.bot.loop.create_task(self.neutral(*args))

        return Loading(self)

    async def add_check(self) -> None:
        """
        Adds a ✅ reaction to the message.
        """
        return await self.message.add_reaction("✅")
    
    async def quietly_delete(message: Message | PartialMessage) -> None:
        if not message.guild:
            return

        if message.channel.permissions_for(message.guild.me).manage_messages:
            with suppress(HTTPException):
                await message.delete()

    async def send(self, *args, **kwargs) -> Message:
        if kwargs.pop("no_reference", False):
            reference = None
        else:
            reference = kwargs.pop("reference", self.message)

        patch = cast(
            Optional[Message],
            kwargs.pop("patch", None),
        )

        embed = cast(
            Optional[Embed],
            kwargs.get("embed"),
        )
        if embed and not embed.color:
            embed.color = self.color

        if args:
            kwargs["content"] = args[0]
            args = ()

        if kwargs.get("content") and len(str(kwargs["content"])) > 2000:
            kwargs["file"] = File(
                BytesIO(str(kwargs["content"]).encode("utf-8")),
                filename="message.txt",
            )
            kwargs["content"] = None

        if file := kwargs.pop("file", None):
            kwargs["files"] = [file]

        if kwargs.get("view") is None:
            kwargs.pop("view", None)

        if patch:
            self.response = await patch.edit(**kwargs)
        else:
            if reference:
                kwargs["reference"] = reference

            try:
                self.response = await super().send(*args, **kwargs)
            except HTTPException:
                kwargs.pop("reference", None)
                self.response = await super().send(*args, **kwargs)

        return self.response

    async def reply(self, *args, **kwargs) -> Message:
        return await self.send(*args, **kwargs)

    async def neutral(
        self,
        *args: str,
        **kwargs,
    ) -> Message:
        """
        Send a neutral embed.
        """
        embed = Embed(
            description="\n".join(
                ("" if len(args) == 1 or index == len(args) - 1 else "") + str(arg)
                for index, arg in enumerate(args)
            ),
            color=kwargs.pop("color", None),
        )
        return await self.send(embed=embed, **kwargs)

    async def approve(
        self,
        *args: str,
        **kwargs,
    ) -> Message:
        """
        Send a success embed.
        """
        embed = Embed(
            description="\n".join(
                ("" if len(args) == 1 or index == len(args) - 1 else "") + str(arg)
                for index, arg in enumerate(args)
            ),
            color=kwargs.pop("color", None),
        )
        return await self.send(embed=embed, **kwargs)

    async def warn(
        self,
        *args: str,
        **kwargs,
    ) -> Message:
        """
        Send an error embed.
        """
        embed = Embed(
            description="\n".join(
                ("" if len(args) == 1 or index == len(args) - 1 else "") + str(arg)
                for index, arg in enumerate(args)
            ),
            color=kwargs.pop("color", None),
        )
        return await self.send(embed=embed, **kwargs)

    async def prompt(
        self,
        *args: str,
        timeout: int = 60,
        delete_after: bool = True,
    ) -> Literal[True]:
        """
        An interactive reaction confirmation dialog.

        Raises UserInputError if the user denies the prompt.
        """
        key = f"prompt:{self.author.id}:{self.command.qualified_name}"
        async with self.bot.redis.get_lock(key):
            embed = Embed(
                description="\n".join(
                    ("" if len(args) == 1 or index == len(args) - 1 else "") + str(arg)
                    for index, arg in enumerate(args)
                ),
            )
            view = Confirmation(self, timeout=timeout)

            try:
                message = await self.send(embed=embed, view=view)
            except HTTPException as exc:
                raise UserInputError("Failed to send prompt message!") from exc

            await view.wait()
            if delete_after:
                await self.quietly_delete(message)

            if view.value is True:
                return True

            raise UserInputError("Confirmation prompt wasn't approved!")

class Embed(discord.Embed):
    def __init__(
        self,
        value: Optional[str] = None,
        *,
        colour: int | Colour | None = None,
        color: int | Colour | None = None,
        title: Any | None = None,
        type: EmbedType = "rich",
        url: Any | None = None,
        description: Any | None = None,
        timestamp: datetime | None = None,
    ):
        description = description or value
        super().__init__(
            colour=colour,
            color=color or Colour.dark_embed(),
            title=title,
            type=type,
            url=url,
            description=description[:4096] if description else None,
            timestamp=timestamp,
        )


discord.Embed = Embed
