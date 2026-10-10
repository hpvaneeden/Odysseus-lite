import ollama
import json

prompt = "What is the weather in Johannesburg?"
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather in a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City and country, e.g. Johannesburg, South Africa"},
                },
                "required": ["location"],
            },
        },
    }
]

response = ollama.chat(
    model="granite4.2:3b",
    messages=[{"role": "user", "content": prompt}],
    tools=tools,
)

print(json.dumps(response, indent=2))
