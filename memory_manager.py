import json
import os
from datetime import datetime

class MemoryManager:
    def __init__(self, storage_file="agent_memory.json"):
        self.storage_file = storage_file
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.storage_file):
            with open(self.storage_file, 'w') as f:
                json.dump({"memory": []}, f)

    def write(self, entry):
        data = self.read_all()
        entry['timestamp'] = datetime.now().isoformat()
        data["memory"].append(entry)
        with open(self.storage_file, 'w') as f:
            json.dump(data, f, indent=4)

    def read_all(self):
        with open(self.storage_file, 'r') as f:
            return json.load(f)

    def prune(self, keep_last=10):
        data = self.read_all()
        if len(data["memory"]) > keep_last:
            data["memory"] = data["memory"][-keep_last:]
            with open(self.storage_file, 'w') as f:
                json.dump(data, f, indent=4)
