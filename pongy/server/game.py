import asyncio
import logging
from types import TracebackType
from typing import Any

from pongy import settings
from pongy.models import WsEvent
from pongy.models import WsGameStateEvent
from pongy.models import WsGameStatePayload
from pongy.models import WsPlayer
from pongy.server.ball import Ball
from pongy.server.ball import IBall
from pongy.server.player import Player
from pongy.server.racket import BottomRacket
from pongy.server.racket import IRacket
from pongy.server.racket import LeftRacket
from pongy.server.racket import RightRacket
from pongy.server.racket import TopRacket

logger = logging.getLogger(__name__)


class Game:
    def __init__(self) -> None:
        self.available_rackets: list[IRacket] = [
            RightRacket(),
            LeftRacket(),
            TopRacket(),
            BottomRacket(),
        ]
        self.players: list[Player] = []
        self.ball: IBall = Ball()
        self._run_task: asyncio.Task[Any] = asyncio.create_task(self.run())

    def add_player(self, player: Player) -> None:
        try:
            player.racket = self.available_rackets.pop()
        except IndexError:
            logger.error("No available racket error")
        self.players.append(player)
        logger.debug("Added new player")

    def remove_player(self, player: Player) -> None:
        player.racket.reset()
        self.available_rackets.append(player.racket)
        self.players[:] = [p for p in self.players if p.uuid != player.uuid]
        logger.debug("Removed player")
        if self.is_empty:
            self._run_task.cancel()

    def bounce(self) -> None:
        new_x, new_y = self.ball.position
        if new_x < 0:
            new_x = 0
            self.ball.angle = 180 - self.ball.angle
            if len(self.players) > 2:
                self.players[2].score += 1
        elif new_x > settings.BOARD_SIZE - settings.BALL_SIZE:
            new_x = settings.BOARD_SIZE - settings.BALL_SIZE
            self.ball.angle = 180 - self.ball.angle
            if len(self.players) > 3:
                self.players[3].score += 1
        if new_y < 0:
            new_y = 0
            self.ball.angle = -self.ball.angle
            if len(self.players) > 1:
                self.players[1].score += 1
        elif new_y > settings.BOARD_SIZE - settings.BALL_SIZE:
            new_y = settings.BOARD_SIZE - settings.BALL_SIZE
            self.ball.angle = -self.ball.angle
            if len(self.players) > 0:
                self.players[0].score += 1
        self.ball.position = int(new_x), int(new_y)

    def to_payload(self) -> WsGameStatePayload:
        return WsGameStatePayload(
            ball_position=self.ball.position,
            players=[
                WsPlayer(
                    uuid=player.uuid,
                    score=player.score,
                    racket_position=player.racket.position,
                )
                for player in self.players
            ],
        )

    async def run(self) -> None:
        while True:
            asyncio.create_task(self.broadcast())
            await asyncio.sleep(1 / settings.FPS)
            self.ball.move()
            for player in self.players:
                player.racket.hit(self.ball)
            self.bounce()

    async def broadcast(self) -> None:
        payload = WsEvent(data=WsGameStateEvent(payload=self.to_payload()))
        await asyncio.gather(
            *(subscriber.ws.send_json(payload.dict()) for subscriber in self.players),
            return_exceptions=True
        )

    @property
    def is_full(self) -> bool:
        return len(self.players) == 4

    @property
    def is_empty(self) -> bool:
        return not self.players


class GamePool:
    _awaiting: Game | None = None

    def __init__(self, player: Player) -> None:
        self._player: Player = player
        self._game: Game | None = None

    async def __aenter__(self) -> Game:
        if not GamePool._awaiting:
            self._game = GamePool._awaiting = Game()
            logger.debug("Created new game")
        else:
            self._game = GamePool._awaiting
        self._game.add_player(self._player)
        if self._game.is_full:
            GamePool._awaiting = None
        return self._game

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._game:
            self._game.remove_player(self._player)
            if GamePool._awaiting is self._game and self._game.is_empty:
                GamePool._awaiting = None
