from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


@lru_cache(maxsize=1)
def get_persona_prompt() -> str:
    path = PROMPTS_DIR / "persona_sunny.txt"
    return path.read_text(encoding="utf-8")


HELP_MENU = (
    "*Sunny's Personal AI Agent*\n\n"
    "I can help you without opening a laptop:\n\n"
    "1. *Emails* — \"check my emails\"\n"
    "2. *Meetings* — \"schedule a meeting tomorrow 3pm with a@b.com\"\n"
    "3. *Repos* — \"check my repos\" (GitHub or Azure DevOps)\n"
    "4. *Code* — pick a repo, review proposed changes here, then *APPROVE* to merge+deploy\n"
    "5. *Status* — \"task status\"\n\n"
    "Reply with a number or just tell me what you need."
)

GREETING_REPLY = (
    "Hi Sunny — I'm your Personal AI Agent.\n\n"
    "I can check emails, schedule meetings, browse GitHub/Azure DevOps repos, "
    "and ship code changes after your approval.\n\n"
    "Say *help* for the menu, or tell me what to do."
)
