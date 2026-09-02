import os
from strands import Agent
from strands.models.openai import OpenAIModel
from dotenv import load_dotenv

load_dotenv()

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
    system_prompt="あなたは簡潔な回答を提供してくれる有能なアシスタントです。"
)

# Send a message to the agent
response = agent("なにか冗談言って")
