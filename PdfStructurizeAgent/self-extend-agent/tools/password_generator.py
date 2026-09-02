from strands import tool
import random
import string

@tool
def password_generator(length: int) -> str:
    """Generate a random password of a given length.

    Args:
        length: The length of the password to generate.

    Returns:
        A random password string.
    """
    characters = string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choice(characters) for _ in range(length))
