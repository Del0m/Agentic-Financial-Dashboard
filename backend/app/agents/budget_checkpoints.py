from langgraph.checkpoint.base import BaseCheckpointSaver

# exit durability also saves on exceptions. filter at the write boundary so a failure before log_advice cannot
# put a transient bill, prompt or balance on disk, even in freed sqlite pages after cleanup
EMPTY = {"signals": [], "signal_actions": {}, "facts": {}, "contexts": {}, "memory": {},
         "diagnoses": {}, "results": [], "flags": []}


def _value(channel, value):
    if channel in EMPTY:
        return EMPTY[channel].copy()
    if channel == "actions":
        return {c: {k: v for k, v in a.items() if k in ("action", "fallback", "downgraded", "escalated")}
                for c, a in value.items()}
    if channel == "__error__":
        return type(value).__name__
    return value


class BudgetCheckpoints(BaseCheckpointSaver):
    def __init__(self, inner):
        super().__init__(serde=inner.serde)
        self.inner = inner

    @property
    def config_specs(self):
        return self.inner.config_specs

    def get_tuple(self, config):
        return self.inner.get_tuple(config)

    def list(self, config, **kwargs):
        return self.inner.list(config, **kwargs)

    def put(self, config, checkpoint, metadata, new_versions):
        clean = {**checkpoint, "channel_values": {k: _value(k, v) for k, v in checkpoint["channel_values"].items()}}
        return self.inner.put(config, clean, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        return self.inner.put_writes(config, [(k, _value(k, v)) for k, v in writes], task_id, task_path)

    def delete_thread(self, thread_id):
        return self.inner.delete_thread(thread_id)

    def get_next_version(self, current, channel):
        return self.inner.get_next_version(current, channel)
