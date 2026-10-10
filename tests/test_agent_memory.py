import unittest
from agent import Agent
from memory_manager import MemoryManager
import os
import json

class TestAgentMemory(unittest.TestCase):
    def setUp(self):
        self.test_file = "test_memory.json"
        # Ensure clean state
        if os.path.exists(self.test_file):
            os.remove(self.test_file)
        self.memory = MemoryManager(storage_file=self.test_file)
        self.agent = Agent()
        # Point agent to test file
        self.agent.memory = self.memory

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_process_adds_to_memory(self):
        self.agent.process("Test Input")
        data = self.memory.read_all()
        # Expecting 2 entries: 1 user, 1 assistant
        self.assertEqual(len(data["memory"]), 2)
        self.assertEqual(data["memory"][0]["content"], "Test Input")
        self.assertEqual(data["memory"][1]["content"], "Processed: Test Input")

    def test_prune_memory(self):
        # Fill with more than 10 entries
        for i in range(15):
            self.agent.process(f"Input {i}")

        data = self.memory.read_all()
        self.assertEqual(len(data["memory"]), 10)

if __name__ == "__main__":
    unittest.main()
