import os
from strands import Agent
from strands.models.openai import OpenAIModel
from strands_tools import shell, file_write
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are a self-extending research agent.

You can CREATE new tools by writing Python files to ./tools/. Each file should
define one or more functions decorated with `@tool` from the strands package.
Tools become available instantly after the file is saved.

Template for a new tool:

```python
from strands import tool

@tool
def my_tool(argument: str) -> str:
    \"\"\"Short description of what this tool does.

    Args:
        argument: What this argument means.

    Returns:
        A string result.
    \"\"\"
    return f"result for {argument}"
```

When a user asks for a capability you don't have, CREATE the tool, then USE it.
Be concise in your replies.
"""

# Initialize your agent
model = OpenAIModel(
    client_args={
        "api_key": os.getenv("OPENAI_API_KEY"),
    },
    # **model_config
    model_id="gpt-4o",
    params={
        "temperature": 0.3,
        "max_tokens": 1024,
    }
)
agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[shell,file_write],
    load_tools_from_directory=True,
)

print("🦆 Self-extending agent. Type 'exit' to quit.\n")
while True:
    try:
        q = input("🦆 ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if q.lower() in ("exit", "quit", "q", ""):
        break
    agent(q)
