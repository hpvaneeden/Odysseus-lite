from memory_manager import MemoryManager

class Agent:
    def __init__(self):
        self.memory = MemoryManager()

    def process(self, user_input):
        # Read current memory
        history = self.memory.read_all()

        # Log interaction
        self.memory.write({"role": "user", "content": user_input})

        # In a real scenario, the model would generate a response here using history
        response = f"Processed: {user_input}"

        # Log response
        self.memory.write({"role": "assistant", "content": response})

        # Prune memory to keep context light (e.g., last 10 entries)
        self.memory.prune(keep_last=10)

        return response

if __name__ == "__main__":
    agent = Agent()
    print(agent.process("Hello, how are you?"))
    print(agent.process("Remember this: I like Python."))
