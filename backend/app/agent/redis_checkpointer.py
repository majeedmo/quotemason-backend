"""Upstash-compatible LangGraph checkpointer.

The official ``langgraph-checkpoint-redis`` RedisSaver requires the RediSearch
module (FT.* commands), which Upstash Redis does not provide — ``setup()``
fails with "Command is not available: 'FT._LIST'". This saver implements the
same BaseCheckpointSaver contract using only core Redis commands
(GET/SET/HSET/ZADD), so it runs on Upstash and any plain Redis.

Key layout (one intake conversation = one thread_id):
    ckpt:{thread}:{ns}:{id}         JSON {checkpoint, metadata, parent_id}
    ckpt_latest:{thread}:{ns}       most recent checkpoint id
    ckpt_index:{thread}:{ns}        zset of checkpoint ids (score 0 — ids are
                                    uuid6, so lexicographic order is temporal)
    ckpt_writes:{thread}:{ns}:{id}  hash of pending writes per task
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (WRITES_IDX_MAP, BaseCheckpointSaver,
                                       ChannelVersions, Checkpoint,
                                       CheckpointMetadata, CheckpointTuple,
                                       get_checkpoint_id)
from redis import Redis


class UpstashRedisSaver(BaseCheckpointSaver[str]):
    def __init__(self, url: str):
        super().__init__()
        self.client = Redis.from_url(url, decode_responses=True)

    # -- serde helpers (serde bytes -> str, Redis values stay text) --------

    def _enc(self, obj: Any) -> str:
        type_, data = self.serde.dumps_typed(obj)
        return json.dumps([type_, base64.b64encode(data).decode()])

    def _dec(self, raw: str) -> Any:
        type_, data = json.loads(raw)
        return self.serde.loads_typed((type_, base64.b64decode(data)))

    @staticmethod
    def _ids(config: RunnableConfig) -> tuple[str, str]:
        conf = config["configurable"]
        return conf["thread_id"], conf.get("checkpoint_ns", "")

    # -- BaseCheckpointSaver interface --------------------------------------

    def put(self, config: RunnableConfig, checkpoint: Checkpoint,
            metadata: CheckpointMetadata,
            new_versions: ChannelVersions) -> RunnableConfig:
        thread_id, ns = self._ids(config)
        checkpoint_id = checkpoint["id"]
        payload = json.dumps({
            "checkpoint": self._enc(checkpoint),
            "metadata": self._enc(metadata),
            "parent_id": config["configurable"].get("checkpoint_id") or "",
        })
        pipe = self.client.pipeline()
        pipe.set(f"ckpt:{thread_id}:{ns}:{checkpoint_id}", payload)
        pipe.set(f"ckpt_latest:{thread_id}:{ns}", checkpoint_id)
        pipe.zadd(f"ckpt_index:{thread_id}:{ns}", {checkpoint_id: 0})
        pipe.execute()
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                 "checkpoint_id": checkpoint_id}}

    def put_writes(self, config: RunnableConfig,
                   writes: list[tuple[str, Any]], task_id: str,
                   task_path: str = "") -> None:
        thread_id, ns = self._ids(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]
        mapping = {}
        for idx, (channel, value) in enumerate(writes):
            order = WRITES_IDX_MAP.get(channel, idx)
            mapping[f"{task_id}:{order:06d}"] = json.dumps(
                {"channel": channel, "value": self._enc(value)})
        if mapping:
            self.client.hset(f"ckpt_writes:{thread_id}:{ns}:{checkpoint_id}",
                             mapping=mapping)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, ns = self._ids(config)
        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            checkpoint_id = self.client.get(f"ckpt_latest:{thread_id}:{ns}")
            if not checkpoint_id:
                return None
        raw = self.client.get(f"ckpt:{thread_id}:{ns}:{checkpoint_id}")
        if not raw:
            return None
        payload = json.loads(raw)
        writes = self.client.hgetall(
            f"ckpt_writes:{thread_id}:{ns}:{checkpoint_id}")
        pending_writes = []
        for field in sorted(writes):
            entry = json.loads(writes[field])
            pending_writes.append((field.rsplit(":", 1)[0], entry["channel"],
                                   self._dec(entry["value"])))
        parent_config = None
        if payload["parent_id"]:
            parent_config = {"configurable": {
                "thread_id": thread_id, "checkpoint_ns": ns,
                "checkpoint_id": payload["parent_id"]}}
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id,
                                     "checkpoint_ns": ns,
                                     "checkpoint_id": checkpoint_id}},
            checkpoint=self._dec(payload["checkpoint"]),
            metadata=self._dec(payload["metadata"]),
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def list(self, config: RunnableConfig | None, *,
             filter: dict[str, Any] | None = None,
             before: RunnableConfig | None = None,
             limit: int | None = None) -> Iterator[CheckpointTuple]:
        if config is None:
            return
        thread_id, ns = self._ids(config)
        ids = self.client.zrange(f"ckpt_index:{thread_id}:{ns}", 0, -1)
        before_id = get_checkpoint_id(before) if before else None
        found = 0
        for checkpoint_id in reversed(ids):  # newest first (uuid6 order)
            if before_id and checkpoint_id >= before_id:
                continue
            tup = self.get_tuple({"configurable": {
                "thread_id": thread_id, "checkpoint_ns": ns,
                "checkpoint_id": checkpoint_id}})
            if tup is None:
                continue
            if filter and any(tup.metadata.get(k) != v
                              for k, v in filter.items()):
                continue
            yield tup
            found += 1
            if limit is not None and found >= limit:
                return
