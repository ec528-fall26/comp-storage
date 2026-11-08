"""The slice request a user sends to the cluster.

A request has three parts, mirroring docs in the project's Job API v0 draft:

    select  which episodes to keep (keywords in the language instruction,
            lab, explicit episode ids, a cap on the count)
    trim    what to cut inside each kept episode (idle frames, cameras,
            frame stride)
    output  the format of the result

Every field has a default, so an empty request is valid: "a small sample of
everything, all cameras, every frame".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CAMERAS = ("wrist", "exterior_1", "exterior_2")
OUTPUT_FORMATS = ("rlds",)  # "lerobot" later, reusing the Demo 2 converter
DEFAULT_MAX_EPISODES = 50


class RequestError(ValueError):
    """The request is malformed; the message says which field and why."""


def _words(values: Any, name: str) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [v for v in values.split(",")]
    if not isinstance(values, (list, tuple)):
        raise RequestError(f"{name} must be a list of strings")
    out = []
    for v in values:
        if not isinstance(v, str):
            raise RequestError(f"{name} must be a list of strings, got {v!r}")
        v = v.strip().lower()
        if v and v not in out:
            out.append(v)
    return out


@dataclass
class Select:
    task_keywords: list[str] = field(default_factory=list)
    object_keywords: list[str] = field(default_factory=list)
    labs: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)
    max_episodes: int | None = DEFAULT_MAX_EPISODES


@dataclass
class Trim:
    drop_idle: bool = False
    cameras: list[str] = field(default_factory=lambda: list(CAMERAS))
    frame_stride: int = 1


@dataclass
class Output:
    format: str = "rlds"


@dataclass
class SliceRequest:
    select: Select = field(default_factory=Select)
    trim: Trim = field(default_factory=Trim)
    output: Output = field(default_factory=Output)

    # ---- construction -------------------------------------------------
    @classmethod
    def build(
        cls,
        tasks: Any = None,
        objects: Any = None,
        labs: Any = None,
        episode_ids: Any = None,
        max_episodes: int | None = DEFAULT_MAX_EPISODES,
        drop_idle: bool = False,
        cameras: Any = None,
        frame_stride: int = 1,
        output_format: str = "rlds",
    ) -> "SliceRequest":
        """Keyword-argument constructor used by the library and the CLI."""
        req = cls(
            select=Select(
                task_keywords=_words(tasks, "tasks"),
                object_keywords=_words(objects, "objects"),
                labs=[l.strip() for l in (labs.split(",") if isinstance(labs, str) else labs or []) if l.strip()],
                episode_ids=[e.strip() for e in (episode_ids.split(",") if isinstance(episode_ids, str) else episode_ids or []) if e.strip()],
                max_episodes=max_episodes,
            ),
            trim=Trim(
                drop_idle=bool(drop_idle),
                cameras=_words(cameras, "cameras") if cameras else list(CAMERAS),
                frame_stride=frame_stride,
            ),
            output=Output(format=output_format),
        )
        req.validate()
        return req

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SliceRequest":
        if not isinstance(d, dict):
            raise RequestError("request must be a JSON object")
        unknown = set(d) - {"select", "trim", "output"}
        if unknown:
            raise RequestError(f"unknown top-level field(s): {', '.join(sorted(unknown))}")
        s, t, o = d.get("select") or {}, d.get("trim") or {}, d.get("output") or {}
        for name, part, allowed in (
            ("select", s, Select.__dataclass_fields__),
            ("trim", t, Trim.__dataclass_fields__),
            ("output", o, Output.__dataclass_fields__),
        ):
            extra = set(part) - set(allowed)
            if extra:
                raise RequestError(f"unknown field(s) in {name}: {', '.join(sorted(extra))}")
        return cls.build(
            tasks=s.get("task_keywords"),
            objects=s.get("object_keywords"),
            labs=s.get("labs"),
            episode_ids=s.get("episode_ids"),
            max_episodes=s.get("max_episodes", DEFAULT_MAX_EPISODES),
            drop_idle=t.get("drop_idle", False),
            cameras=t.get("cameras"),
            frame_stride=t.get("frame_stride", 1),
            output_format=o.get("format", "rlds"),
        )

    @classmethod
    def load(cls, path: str | Path) -> "SliceRequest":
        try:
            data = json.loads(Path(path).read_text())
        except json.JSONDecodeError as e:
            raise RequestError(f"{path} is not valid JSON: {e}") from None
        return cls.from_dict(data)

    # ---- checks and serialisation --------------------------------------
    def validate(self) -> None:
        m = self.select.max_episodes
        if m is not None and (not isinstance(m, int) or isinstance(m, bool) or m < 1):
            raise RequestError("max_episodes must be a positive integer or null (no cap)")
        bad = [c for c in self.trim.cameras if c not in CAMERAS]
        if bad:
            raise RequestError(f"unknown camera(s) {bad}; choose from {list(CAMERAS)}")
        if not self.trim.cameras:
            raise RequestError("keep at least one camera")
        fs = self.trim.frame_stride
        if not isinstance(fs, int) or isinstance(fs, bool) or fs < 1:
            raise RequestError("frame_stride must be an integer >= 1 (15 keeps 1 frame per second)")
        if self.output.format not in OUTPUT_FORMATS:
            raise RequestError(f"output format {self.output.format!r} not supported yet; choose from {list(OUTPUT_FORMATS)}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json() + "\n")

    def summary(self) -> str:
        s, t = self.select, self.trim
        parts = []
        parts.append(f"tasks={','.join(s.task_keywords) or 'any'}")
        parts.append(f"objects={','.join(s.object_keywords) or 'any'}")
        if s.labs:
            parts.append(f"labs={','.join(s.labs)}")
        if s.episode_ids:
            parts.append(f"episodes={len(s.episode_ids)} ids")
        parts.append(f"max={s.max_episodes if s.max_episodes is not None else 'all'}")
        parts.append(f"drop_idle={'yes' if t.drop_idle else 'no'}")
        parts.append(f"cameras={','.join(t.cameras)}")
        parts.append(f"stride={t.frame_stride}")
        return " ".join(parts)
