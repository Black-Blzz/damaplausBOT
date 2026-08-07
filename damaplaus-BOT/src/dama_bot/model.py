from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Color(str, Enum):
    BLACK = "black"
    WHITE = "white"

    def other(self) -> "Color":
        return Color.WHITE if self is Color.BLACK else Color.BLACK


@dataclass(frozen=True, slots=True)
class Piece:
    color: Color
    king: bool = False


Square = tuple[int, int]  # (row, column), 0 <= coordinate < 8


@dataclass(frozen=True, slots=True)
class Move:
    path: tuple[Square, ...]
    captures: tuple[Square, ...] = ()

    @property
    def is_capture(self) -> bool:
        return bool(self.captures)


@dataclass(frozen=True, slots=True)
class Position:
    pieces: tuple[tuple[Square, Piece], ...]
    turn: Color

    @classmethod
    def from_mapping(cls, pieces: dict[Square, Piece], turn: Color) -> "Position":
        return cls(tuple(sorted(pieces.items())), turn)

    def mapping(self) -> dict[Square, Piece]:
        return dict(self.pieces)
