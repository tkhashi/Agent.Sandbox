#Import Agent and tools
import logging
import os

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException
from strands import Agent, tool
from strands.models.openai import OpenAIModel
from dotenv import load_dotenv

load_dotenv()


# Configure logging
logging.getLogger("strands").setLevel(
    logging.INFO
)  # Set to DEBUG for more detailed logs


# Define a websearch tool
@tool
def websearch(
    keywords: str, region: str = "us-en", max_results: int | None = None
) -> str:
    """Search the web to get updated information.
    Args:
        keywords (str): The search query keywords.
        region (str): The search region: wt-wt, us-en, uk-en, ru-ru, etc..
        max_results (int | None): The maximum number of results to return.
    Returns:
        List of dictionaries with search results.
    """
    try:
        results = DDGS().text(keywords, region=region, max_results=max_results)
        return results if results else "No results found."
    except RatelimitException:
        return "RatelimitException: Please try again after a short delay."
    except DDGSException as d:
        return f"DuckDuckGoSearchException: {d}"
    except Exception as e:
        return f"Exception: {e}"


# Create a recipe assistant agent

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
recipe_agent = Agent(
    model=model,
    system_prompt="""あなたは役に立つ料理アシスタントのレシピボットです。
    ユーザーが材料に基づいてレシピを見つけたり、料理に関する質問に答えたりできるようにします。
    ユーザーが材料について言及したときにレシピを検索したり、料理情報を検索したりするには、Web検索ツールを使用します。""",
    tools=[websearch],
)


if __name__ == "__main__":
    print("\nRecipeBot: Ask me about recipes or cooking! Type 'exit' to quit.\n")

    # Run the agent in a loop for interactive conversation
    while True:
        user_input = input("\nYou > ")
        if user_input.lower() == "exit":
            print("Happy cooking!")
            break
        response = recipe_agent(user_input)
        print(f"\nRecipeBot > {response}")