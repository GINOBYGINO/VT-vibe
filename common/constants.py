DEFAULT_TEST_URL = "https://www.youtube.com/watch?v=d6wJVaDzNBE"

STEP_NAMES = (
    "01_download",
    "02_asr",
    "03_highlights",
    "04_edit",
    "05_subtitle",
)

STEP_INDEX = {name: i + 1 for i, name in enumerate(STEP_NAMES)}
