DEFAULT_TEST_URL = "https://www.youtube.com/watch?v=d6wJVaDzNBE"

# Single source of truth for the current pipeline / export version.
PIPELINE_VERSION = "0.15"

STEP_NAMES = (
    "01_download",
    "02_asr",
    "03_highlights",
    "04_edit",
    "05_subtitle",
    "06_effects",
    "07_flourish",
    "08_hook",
)

STEP_INDEX = {name: i + 1 for i, name in enumerate(STEP_NAMES)}

# Friendly aliases for regression / communication during tool development
TEST_ALIASES = {
    "1": "test1",
    "2": "test2",
    "3": "test3",
    "4": "test4",
    "5": "test5",
    "6": "test6",
    "7": "test7",
}

REGRESSION_URLS = {
    "1": DEFAULT_TEST_URL,  # test1
    "2": "https://www.youtube.com/watch?v=PjMOuWoBiAY",  # test2 talk
    "3": "https://www.youtube.com/watch?v=KWcF-F0ozQ8",  # test3 game
    "4": "https://www.youtube.com/watch?v=C_Q3RlZLRXM",  # test4 emotion
    "5": "https://www.youtube.com/watch?v=eeUK3CTWjbU",  # test5 stable-subs
    "6": "https://www.youtube.com/watch?v=XqFwdmtj500",  # test6
    "7": "https://www.youtube.com/live/V2xvIm2lLGs",  # test7 official clip reference
}

VIDEO_ID_TO_ALIAS = {
    "d6wJVaDzNBE": "test1",
    "PjMOuWoBiAY": "test2",
    "KWcF-F0ozQ8": "test3",
    "C_Q3RlZLRXM": "test4",
    "eeUK3CTWjbU": "test5",
    "XqFwdmtj500": "test6",
    "V2xvIm2lLGs": "test7",
}


def alias_from_url(url: str) -> str | None:
    for vid, alias in VIDEO_ID_TO_ALIAS.items():
        if vid in (url or ""):
            return alias
    return None
